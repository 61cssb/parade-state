"""IPPT monitoring ingest + read models (docs/IPPT_MONITORING.md).

Ingest implements §4: one submission = the full six-file snapshot for
one report date, atomic per report date (all six files land, or none
do). Re-uploading a report date replaces the previous ingest of that
date, so corrections and repeated test runs are idempotent. Row-level
validation failures land in the quarantine list instead of rejecting
the file; a serviceman may appear in at most one file per snapshot —
extra occurrences are quarantined rather than crashing the ingest.

Identity follows §3.2: the spine is keyed by normalized full name; an
existing personnel link is never re-resolved while the Personnel row
still exists; unique name hits on the attendance-active roll link
automatically, homonyms are flagged ``ambiguous`` (display-only for
now), misses stay ``unmatched`` and are retried on later ingests.
Servicemen excluded from tracking (decided 2026-10-08) keep their rows
but are hidden from the read models; a snapshot that shows them again
surfaces them in the upload response for re-inclusion.

Ingest order never matters (decided 2026-10-08): display identity
(rank/sub-unit) comes from the latest observation rather than the
spine's last-written values, and every ingest ends with a recompute
pass over ALL snapshots in report-date order that re-derives the
order-dependent derivations — COMPLETED-row window backfills and
screening ``screened_on`` dates — so backfilling an old month cannot
leave NULL window links or lost screening dates behind. A derived
window that overlaps (±45 days) but disagrees with an existing window
of the same serviceman is still ingested, but records a consistency
warning in the upload response (§6.1's cheap consistency check) instead
of silently looking like a rollover.

The read models fold the observations into the dashboard landing state
(latest observation per serviceman) and the escalation tiers of §6.4 —
banded by months before window close (3 = most urgent … 9 = least),
computed in Python so both dialects behave identically (the spec's §6.3
views are PostgreSQL prose, not DDL this app ships).
"""

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import NominalRoll, Personnel
from parade_state.models.ippt import (
    FILE_KINDS,
    STATE_LABELS,
    IpptHealthScreening,
    IpptQuarantinedRow,
    IpptServiceman,
    IpptSnapshot,
    IpptStateObservation,
    IpptWindow,
)
from parade_state.utils import utc_dt
from parade_state.utils.ippt_reports import (
    FULFILLED_STATES,
    IpptReportRow,
    ParsedReportFile,
    months_between,
    normalize_name,
    parse_report_file,
    tier_band,
    tier_highlighted,
    window_dates,
)

logger = logging.getLogger(__name__)


class IngestError(ValueError):
    """The submission is rejected whole (incomplete set, mixed report
    dates, or an unusable file) — nothing lands (§4 atomicity)."""


#: A derived window this many days from an existing window of the same
#: serviceman (but not equal) is the same window with inconsistent
#: close data, not a rollover — rollovers jump ~1 year (§6.1's cheap
#: consistency check). Still ingested; recorded as a warning.
WINDOW_CONSISTENCY_TOLERANCE_DAYS = 45


@dataclass
class IngestResult:
    report_date: date
    replaced: bool
    per_kind: dict[str, dict[str, int]]
    match_counts: dict[str, int]
    quarantined_total: int
    window_warnings: list[dict] = field(default_factory=list)
    excluded_reappeared: list[dict] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(kind["rows"] for kind in self.per_kind.values())


# ============================================================================
# Ingest
# ============================================================================


async def ingest_snapshot(
    db: AsyncSession,
    files: list[tuple[str, bytes]],
) -> IngestResult:
    """Ingest the six-file snapshot for one report date, atomically.

    ``files`` is a list of ``(filename, content_bytes)``. File names
    must be canonical (kind + date come from the name); per-row
    problems quarantine instead of rejecting.

    Raises:
        IngestError: the submission is rejected whole — wrong file set,
            mixed report dates, or a file-level parse error. Nothing is
            written (the caller's session rolls back / never commits).
    """
    parsed_files: list[ParsedReportFile] = []
    for filename, content in files:
        try:
            parsed_files.append(parse_report_file(filename, content))
        except ValueError as exc:
            raise IngestError(str(exc)) from exc

    kinds = sorted(parsed.file_kind for parsed in parsed_files)
    if kinds != sorted(FILE_KINDS):
        raise IngestError(
            "A snapshot is the full set of six files for one report date "
            f"(§4); got: {', '.join(kinds) or 'none'}"
        )
    dates = {parsed.report_date for parsed in parsed_files}
    if len(dates) > 1:
        raise IngestError(
            "All six files must carry the same report date; got: "
            + ", ".join(sorted(d.isoformat() for d in dates))
        )
    report_date = dates.pop()

    # Replace-on-re-ingest: drop the previous ingest of this date.
    # Explicit child deletes keep SQLite (no FK enforcement) consistent;
    # on Postgres the ON DELETE CASCADE would do the same.
    existing = (
        (
            await db.execute(
                select(IpptSnapshot).where(IpptSnapshot.report_date == report_date)
            )
        )
        .scalars()
        .all()
    )
    replaced = bool(existing)
    existing_ids = [snapshot.id for snapshot in existing]
    if existing_ids:
        await db.execute(
            delete(IpptStateObservation).where(
                IpptStateObservation.snapshot_id.in_(existing_ids)
            )
        )
        await db.execute(
            delete(IpptQuarantinedRow).where(
                IpptQuarantinedRow.snapshot_id.in_(existing_ids)
            )
        )
        await db.execute(delete(IpptSnapshot).where(IpptSnapshot.id.in_(existing_ids)))
    await db.execute(
        delete(IpptHealthScreening).where(
            IpptHealthScreening.observed_on == report_date
        )
    )

    match_counts = {"matched": 0, "unmatched": 0, "ambiguous": 0}
    quarantined_total = 0
    window_warnings: list[dict] = []
    excluded_reappeared: list[dict] = []
    seen_servicemen: set[int] = set()
    match_counted: set[int] = set()

    # Deterministic order: outstanding files first, so a COMPLETED row's
    # window backfill can also fall back to a same-date outstanding row
    # if a unit ever issues those together (otherwise the post-ingest
    # recompute pass derives the link from any snapshot's data).
    order = {kind: index for index, kind in enumerate(FILE_KINDS)}
    parsed_files.sort(key=lambda parsed: order[parsed.file_kind])

    for parsed in parsed_files:
        snapshot = IpptSnapshot(
            report_date=report_date,
            file_kind=parsed.file_kind,
            filename=parsed.filename,
            row_count=len(parsed.rows),
        )
        db.add(snapshot)
        await db.flush()

        for entry in parsed.quarantined:
            db.add(
                IpptQuarantinedRow(
                    snapshot_id=snapshot.id,
                    line_number=entry.line_number,
                    raw_row=entry.raw_row,
                    reason=entry.reason,
                )
            )
            quarantined_total += 1

        for row in parsed.rows:
            serviceman, match_status = await _resolve_serviceman(db, row)
            # Count each serviceman's match once per ingest, even when a
            # retried (unmatched/ambiguous) match re-evaluates on a later
            # file occurrence or re-ingest.
            if match_status is not None and serviceman.id not in match_counted:
                match_counts[match_status] += 1
                match_counted.add(serviceman.id)

            if serviceman.id in seen_servicemen:
                db.add(
                    IpptQuarantinedRow(
                        snapshot_id=snapshot.id,
                        line_number=row.line_number,
                        raw_row=f"{row.rank},{row.full_name}",
                        reason=(
                            "serviceman appears in more than one file of "
                            "this snapshot; keeping the earlier occurrence"
                        ),
                    )
                )
                quarantined_total += 1
                continue
            seen_servicemen.add(serviceman.id)

            if serviceman.excluded:
                # Remove-from-tracking keeps ingesting (rows retained);
                # the reappeared-excluded person is surfaced so the
                # super-admin can re-include them (decided 2026-10-08).
                excluded_reappeared.append(
                    {
                        "serviceman_id": serviceman.id,
                        "full_name": serviceman.full_name,
                        "exclusion_reason": serviceman.exclusion_reason,
                    }
                )

            window_id, window_warning = await _resolve_window(
                db, serviceman, row, report_date
            )
            if window_warning is not None:
                window_warnings.append(
                    {
                        "serviceman_id": serviceman.id,
                        "full_name": serviceman.full_name,
                        "report_date": report_date.isoformat(),
                        "existing_window_end": window_warning[0].isoformat(),
                        "derived_window_end": window_warning[1].isoformat(),
                    }
                )
            db.add(
                IpptStateObservation(
                    snapshot_id=snapshot.id,
                    serviceman_id=serviceman.id,
                    window_id=window_id,
                    rank=row.rank,
                    sub_unit=row.sub_unit,
                    state=row.state,
                    ippt_status=row.ippt_status,
                    fit_sessions=row.fit_sessions,
                    window_close_days=row.window_close_days,
                    booked=row.booked,
                    ffi=row.ffi,
                    reminder_sent=row.reminder_sent,
                    last_sent=row.last_sent,
                )
            )
            if row.ffi is not None:
                await _record_screening(db, serviceman.id, report_date, row.ffi)

    # Order-independent derivations: replay ALL snapshots in report-date
    # order so backfilled months link windows and gain screening dates no
    # matter when their snapshot was uploaded (decided 2026-10-08).
    await recompute_derivations(db)

    result = IngestResult(
        report_date=report_date,
        replaced=replaced,
        per_kind={
            parsed.file_kind: {
                "rows": len(parsed.rows),
                "quarantined": len(parsed.quarantined),
            }
            for parsed in parsed_files
        },
        match_counts=match_counts,
        quarantined_total=quarantined_total,
        window_warnings=window_warnings,
        excluded_reappeared=excluded_reappeared,
    )
    logger.info(
        "IPPT snapshot %s ingested: %d rows, %d quarantined%s, "
        "%d window warnings, %d excluded reappeared",
        report_date.isoformat(),
        result.total_rows,
        quarantined_total,
        " (replaced previous ingest)" if replaced else "",
        len(window_warnings),
        len(excluded_reappeared),
    )
    return result


async def _resolve_serviceman(
    db: AsyncSession, row: IpptReportRow
) -> tuple[IpptServiceman, str | None]:
    """Find or create the spine row for a report row (§3.2).

    Returns the serviceman and, when this call set/refreshed the match,
    the new ``match_status`` (``None`` = untouched existing link, rule 1).
    """
    norm = normalize_name(row.full_name)
    serviceman = (
        await db.execute(
            select(IpptServiceman).where(IpptServiceman.full_name_norm == norm)
        )
    ).scalar_one_or_none()

    if serviceman is None:
        personnel_id, match_status = await _match_personnel(db, norm)
        serviceman = IpptServiceman(
            rank=row.rank,
            full_name=row.full_name,
            full_name_norm=norm,
            unit=row.unit,
            sub_unit=row.sub_unit,
            personnel_id=personnel_id,
            match_status=match_status,
        )
        db.add(serviceman)
        await db.flush()
        return serviceman, match_status

    # Idempotent re-ingest: same spine row, refreshed to the latest seen.
    serviceman.rank = row.rank
    serviceman.unit = row.unit
    serviceman.sub_unit = row.sub_unit
    serviceman.updated_at = utc_dt.ensure_naive(utc_dt.utcnow())

    if serviceman.personnel_id is not None:
        live = await db.get(Personnel, serviceman.personnel_id)
        if live is not None:
            return serviceman, None  # rule 1: an existing link is authoritative
        # The linked Personnel row is gone (e.g. the roll was replaced):
        # the link has fallen off, so resolve again.
    personnel_id, match_status = await _match_personnel(db, norm)
    serviceman.personnel_id = personnel_id
    serviceman.match_status = match_status
    return serviceman, match_status


async def _match_personnel(db: AsyncSession, norm_name: str) -> tuple[str | None, str]:
    """Match a normalized name against the attendance-active roll (§3.2).

    Returns ``(personnel_id, match_status)``: unique hit → linked,
    homonyms → ``ambiguous`` (no link, flagged), miss → ``unmatched``.
    """
    active_roll_id = (
        await db.execute(
            select(NominalRoll.id).where(NominalRoll.attendance_active.is_(True))
        )
    ).scalar_one_or_none()
    if active_roll_id is None:
        return None, "unmatched"

    candidates = (
        (
            await db.execute(
                select(Personnel).where(
                    Personnel.nominal_roll_id == str(active_roll_id),
                    Personnel.status == "active",
                    func.upper(Personnel.full_name) == norm_name,
                )
            )
        )
        .scalars()
        .all()
    )
    # Whitespace-normalize in Python; the SQL upper() is just the index-
    # friendly pre-filter.
    candidates = [
        person for person in candidates if normalize_name(person.full_name) == norm_name
    ]
    if len(candidates) == 1:
        return str(candidates[0].id), "matched"
    if len(candidates) > 1:
        return None, "ambiguous"
    return None, "unmatched"


async def _resolve_window(
    db: AsyncSession,
    serviceman: IpptServiceman,
    row: IpptReportRow,
    report_date: date,
) -> tuple[int | None, tuple[date, date] | None]:
    """Window id for an observation: derived from ``Window close`` for
    outstanding rows, backfilled from the serviceman's covering window
    for ``*_COMPLETED`` rows (§6.1).

    Returns ``(window_id, warning)``. ``warning`` is a
    ``(existing_window_end, derived_window_end)`` pair when the derived
    window overlaps (±45 days) but disagrees with an existing window of
    the same serviceman — the same window with inconsistent close data,
    not a rollover (rollovers jump ~1 year). The row is still ingested;
    the caller surfaces the warning in the upload response.
    """
    if row.window_close_days is not None:
        window_start, window_end = window_dates(report_date, row.window_close_days)
        others = (
            (
                await db.execute(
                    select(IpptWindow).where(
                        IpptWindow.serviceman_id == serviceman.id,
                        IpptWindow.window_end != window_end,
                    )
                )
            )
            .scalars()
            .all()
        )
        warning: tuple[date, date] | None = None
        for candidate in sorted(others, key=lambda w: w.window_end):
            if (
                abs((candidate.window_end - window_end).days)
                <= WINDOW_CONSISTENCY_TOLERANCE_DAYS
            ):
                warning = (candidate.window_end, window_end)
                break

        window = (
            await db.execute(
                select(IpptWindow).where(
                    IpptWindow.serviceman_id == serviceman.id,
                    IpptWindow.window_end == window_end,
                )
            )
        ).scalar_one_or_none()
        if window is None:
            window = IpptWindow(
                serviceman_id=serviceman.id,
                window_end=window_end,
                window_start=window_start,
            )
            db.add(window)
            await db.flush()
        return window.id, warning

    # Completed rows carry no window close: backfill from the serviceman's
    # window covering this report date, when one is already known (the
    # post-ingest recompute pass re-derives this over ALL snapshots, so a
    # window defined only by a later-ingested snapshot still links up).
    return (
        (
            await db.execute(
                select(IpptWindow.id)
                .where(
                    IpptWindow.serviceman_id == serviceman.id,
                    IpptWindow.window_start <= report_date,
                    IpptWindow.window_end >= report_date,
                )
                .order_by(IpptWindow.window_start.desc())
                .limit(1)
            )
        ).scalar_one_or_none(),
        None,
    )


async def _record_screening(
    db: AsyncSession, serviceman_id: int, observed_on: date, status: str
) -> None:
    """Upsert the FFI screening observation. ``screened_on`` is NOT set
    here — it is a derived value owned by :func:`recompute_derivations`,
    which runs after every ingest and re-derives it from the full
    screening history regardless of ingest order (§6.1)."""
    screening = (
        await db.execute(
            select(IpptHealthScreening).where(
                IpptHealthScreening.serviceman_id == serviceman_id,
                IpptHealthScreening.observed_on == observed_on,
            )
        )
    ).scalar_one_or_none()
    if screening is None:
        screening = IpptHealthScreening(
            serviceman_id=serviceman_id,
            observed_on=observed_on,
            status=status,
        )
        db.add(screening)
    else:  # re-ingest of this date
        screening.status = status


async def recompute_derivations(db: AsyncSession) -> None:
    """Re-derive the order-dependent ingest derivations over ALL
    snapshots, replaying in report-date order (decided 2026-10-08).

    - ``*_COMPLETED`` observations (no ``window_close_days``) re-link to
      the serviceman's window covering their report date, so a backfilled
      old month links up even when the defining outstanding row arrived
      in a later-ingested snapshot or a later report date, and same-date
      COMPLETED+outstanding pairs link regardless of file order. Stays
      NULL only when no known window covers the date.
    - ``ippt_health_screenings.screened_on`` is re-derived as the
      ``observed_on`` of each 'fit' row with a strictly-earlier 'pending'
      row — the pending→fit flip (§6.1) — so the flip date survives any
      ingest order.

    Runs inside the ingest transaction (before the caller commits).
    """
    windows = (await db.execute(select(IpptWindow))).scalars().all()
    by_serviceman: dict[int, list[IpptWindow]] = {}
    for window in windows:
        by_serviceman.setdefault(window.serviceman_id, []).append(window)
    for window_list in by_serviceman.values():
        window_list.sort(key=lambda w: (w.window_start, w.window_end))

    completed_rows = (
        await db.execute(
            select(IpptStateObservation, IpptSnapshot.report_date)
            .join(IpptSnapshot, IpptStateObservation.snapshot_id == IpptSnapshot.id)
            .where(IpptStateObservation.window_close_days.is_(None))
            .order_by(IpptSnapshot.report_date, IpptStateObservation.id)
        )
    ).all()
    for observation, report_date in completed_rows:
        covering = [
            window
            for window in by_serviceman.get(observation.serviceman_id, [])
            if window.window_start <= report_date <= window.window_end
        ]
        # Latest window_start wins (windows of one serviceman do not
        # legitimately overlap; ties mean inconsistent data — item 3 warns).
        relinked = covering[-1] if covering else None
        linked_id = relinked.id if relinked is not None else None
        if observation.window_id != linked_id:
            observation.window_id = linked_id

    screenings = (
        (
            await db.execute(
                select(IpptHealthScreening).order_by(
                    IpptHealthScreening.serviceman_id,
                    IpptHealthScreening.observed_on,
                )
            )
        )
        .scalars()
        .all()
    )
    pending_seen = False
    current_serviceman: int | None = None
    for screening in screenings:
        if screening.serviceman_id != current_serviceman:
            current_serviceman = screening.serviceman_id
            pending_seen = False
        expected = (
            screening.observed_on
            if (screening.status == "fit" and pending_seen)
            else None
        )
        if screening.screened_on != expected:
            screening.screened_on = expected
        if screening.status == "pending":
            pending_seen = True


# ============================================================================
# Read models
# ============================================================================


@dataclass
class ServicemanState:
    """A serviceman's dashboard landing state: latest observation + tier."""

    serviceman_id: int
    personnel_id: str | None
    rank: str
    full_name: str
    sub_unit: str
    match_status: str | None
    state: str
    state_label: str
    ippt_status: str | None
    fit_sessions: int | None
    booked: bool | None
    ffi: str | None
    report_date: date
    window_end: date | None
    days_to_close: int | None
    months_elapsed: int | None
    months_to_close: int | None
    tier: str | None
    highlighted: bool
    fulfilled: bool
    stale: bool

    def to_dict(self) -> dict:
        return {
            "serviceman_id": self.serviceman_id,
            "personnel_id": self.personnel_id,
            "rank": self.rank,
            "full_name": self.full_name,
            "sub_unit": self.sub_unit,
            "match_status": self.match_status,
            "state": self.state,
            "state_label": self.state_label,
            "ippt_status": self.ippt_status,
            "fit_sessions": self.fit_sessions,
            "booked": self.booked,
            "ffi": self.ffi,
            "report_date": self.report_date.isoformat(),
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "days_to_close": self.days_to_close,
            "months_elapsed": self.months_elapsed,
            "months_to_close": self.months_to_close,
            "tier": self.tier,
            "highlighted": self.highlighted,
            "fulfilled": self.fulfilled,
            "stale": self.stale,
        }


async def latest_states(db: AsyncSession) -> list[ServicemanState]:
    """The dashboard landing state: latest observation per serviceman
    (§6.3 ``ippt_current_state``) with the §6.4 tier assignment.

    Servicemen removed from tracking (``excluded``) are hidden here —
    the dashboard and tier views never show them (decided 2026-10-08).
    Display identity (rank, sub-unit) comes from the latest observation,
    not the spine's last-written values, so which snapshot arrived first
    never changes what the dashboard shows.
    """
    rows = (
        await db.execute(
            select(
                IpptStateObservation,
                IpptSnapshot.report_date,
                IpptServiceman,
                IpptWindow,
            )
            .join(IpptSnapshot, IpptStateObservation.snapshot_id == IpptSnapshot.id)
            .join(
                IpptServiceman, IpptStateObservation.serviceman_id == IpptServiceman.id
            )
            .outerjoin(IpptWindow, IpptStateObservation.window_id == IpptWindow.id)
            .where(IpptServiceman.excluded.is_(False))
        )
    ).all()

    # Latest observation per serviceman, plus the most recent non-null
    # FFI value (only Family C files carry it, so the latest known
    # screening may predate the latest observation; §6.3 caveat).
    latest: dict[int, tuple] = {}
    latest_ffi: dict[int, tuple[str, date]] = {}
    for observation, report_date, serviceman, window in rows:
        current = latest.get(serviceman.id)
        if current is None or (report_date, observation.id) > (
            current[1],
            current[0].id,
        ):
            latest[serviceman.id] = (observation, report_date, serviceman, window)
        if observation.ffi is not None:
            known = latest_ffi.get(serviceman.id)
            if known is None or report_date >= known[1]:
                latest_ffi[serviceman.id] = (observation.ffi, report_date)

    today = utc_dt.utcnow().date()
    newest_report = max(
        (report_date for (_, report_date, _, _) in latest.values()),
        default=date.min,  # empty dashboard: the loop below never runs
    )

    # Effective match status: a stored "matched" link whose Personnel row
    # no longer resolves (e.g. the roll was replaced since the last ingest)
    # displays as not-in-nominal-roll until the next ingest re-resolves it.
    linked_ids = [
        serviceman.personnel_id
        for (observation, report_date, serviceman, window) in latest.values()
        if serviceman.personnel_id is not None
    ]
    resolved_ids: set[str] = set()
    if linked_ids:
        resolved_ids = set(
            (await db.execute(select(Personnel.id).where(Personnel.id.in_(linked_ids))))
            .scalars()
            .all()
        )

    states: list[ServicemanState] = []
    for serviceman_id, (observation, report_date, serviceman, window) in latest.items():
        months_elapsed = (
            months_between(window.window_start, report_date)
            if window is not None
            else None
        )
        # Tiers are keyed to proximity of the window close (the unit's
        # "N months before window close" nomenclature).
        months_to_close = (
            months_between(report_date, window.window_end)
            if window is not None
            else None
        )
        # Observations whose window already closed stay outside the
        # tiers — post-close outcomes are defaulter territory (§6.4).
        in_window = window is not None and window.window_end >= report_date
        band = tier_band(months_to_close) if in_window else None
        fulfilled = observation.state in FULFILLED_STATES
        tier = None if fulfilled else band
        known_ffi = latest_ffi.get(serviceman_id)
        ffi = known_ffi[0] if known_ffi is not None else None
        if (
            serviceman.match_status == "matched"
            and serviceman.personnel_id not in resolved_ids
        ):
            match_status = "unmatched"
        else:
            match_status = serviceman.match_status
        states.append(
            ServicemanState(
                serviceman_id=serviceman_id,
                personnel_id=(
                    serviceman.personnel_id if match_status == "matched" else None
                ),
                rank=observation.rank,
                full_name=serviceman.full_name,
                sub_unit=observation.sub_unit,
                match_status=match_status,
                state=observation.state,
                state_label=STATE_LABELS[observation.state],
                ippt_status=observation.ippt_status,
                fit_sessions=observation.fit_sessions,
                booked=observation.booked,
                ffi=ffi,
                report_date=report_date,
                window_end=window.window_end if window is not None else None,
                days_to_close=(
                    (window.window_end - today).days if window is not None else None
                ),
                months_elapsed=months_elapsed,
                months_to_close=months_to_close,
                tier=tier,
                highlighted=tier_highlighted(
                    band, observation.state, observation.booked, ffi
                ),
                fulfilled=fulfilled,
                stale=report_date < newest_report,
            )
        )
    states.sort(key=_display_order)
    return states


async def dashboard_data(db: AsyncSession) -> dict:
    """Everything the dashboard page/API needs in one payload."""
    states = await latest_states(db)
    tiers: dict[str, list[ServicemanState]] = {
        "tier_3": [],
        "tier_6": [],
        "tier_9": [],
    }
    fulfilled: list[ServicemanState] = []
    for entry in states:
        if entry.highlighted and entry.tier is not None:
            tiers[entry.tier].append(entry)
        elif entry.fulfilled:
            fulfilled.append(entry)

    match_counts = {"matched": 0, "unmatched": 0, "ambiguous": 0}
    for entry in states:
        if entry.match_status in match_counts:
            match_counts[entry.match_status] += 1

    quarantined_total = (
        await db.scalar(select(func.count()).select_from(IpptQuarantinedRow))
    ) or 0

    return {
        "servicemen": [entry.to_dict() for entry in states],
        "tiers": {
            band: [entry.to_dict() for entry in entries]
            for band, entries in tiers.items()
        },
        "fulfilled": [entry.to_dict() for entry in fulfilled],
        "summary": {
            "total": len(states),
            "fulfilled": len(fulfilled),
            "highlighted": sum(len(entries) for entries in tiers.values()),
            "states": dict(
                sorted(
                    _count_by(states, lambda entry: entry.state).items(),
                )
            ),
            "match": match_counts,
            "quarantined": quarantined_total,
            "excluded": await excluded_count(db),
            "report_dates": await report_date_count(db),
            "latest_report_date": await latest_report_date(db),
        },
    }


async def snapshot_history(db: AsyncSession) -> list[dict]:
    """Per-report-date ingest history with per-file row/quarantine counts."""
    snapshots = (
        (
            await db.execute(
                select(IpptSnapshot).order_by(
                    IpptSnapshot.report_date.desc(), IpptSnapshot.file_kind
                )
            )
        )
        .scalars()
        .all()
    )
    quarantine_counts: dict[int, int] = dict(
        (
            await db.execute(
                select(
                    IpptQuarantinedRow.snapshot_id,
                    func.count(),
                ).group_by(IpptQuarantinedRow.snapshot_id)
            )
        ).all()
    )

    by_date: dict[date, dict] = {}
    for snapshot in snapshots:
        day = by_date.setdefault(
            snapshot.report_date,
            {"report_date": snapshot.report_date.isoformat(), "files": []},
        )
        day["files"].append(
            {
                "file_kind": snapshot.file_kind,
                "filename": snapshot.filename,
                "row_count": snapshot.row_count,
                "quarantined": quarantine_counts.get(snapshot.id, 0),
                "ingested_at": utc_dt.ensure_naive(snapshot.ingested_at).isoformat(
                    sep=" ", timespec="seconds"
                ),
            }
        )
    return list(by_date.values())


async def quarantined_rows(db: AsyncSession) -> list[dict]:
    """The reject list, newest first, for the upload page's review panel."""
    rows = (
        await db.execute(
            select(IpptQuarantinedRow, IpptSnapshot)
            .join(
                IpptSnapshot,
                IpptQuarantinedRow.snapshot_id == IpptSnapshot.id,
            )
            .order_by(
                IpptSnapshot.report_date.desc(),
                IpptQuarantinedRow.snapshot_id,
                IpptQuarantinedRow.line_number,
            )
        )
    ).all()
    return [
        {
            "report_date": snapshot.report_date.isoformat(),
            "file_kind": snapshot.file_kind,
            "filename": snapshot.filename,
            "line_number": row.line_number,
            "raw_row": row.raw_row,
            "reason": row.reason,
        }
        for row, snapshot in rows
    ]


async def set_tracking_exclusion(
    db: AsyncSession,
    serviceman_id: int,
    excluded: bool,
    reason: str | None = None,
) -> IpptServiceman | None:
    """Remove-from-tracking / re-include (decided 2026-10-08).

    Sets ``excluded`` (+ ``exclusion_reason`` when excluding; clearing on
    re-inclusion). Rows and their history are always retained, and a
    later snapshot showing the person again does NOT lift the exclusion —
    it surfaces them in the upload response instead. Returns the updated
    spine row, or ``None`` for an unknown id.
    """
    serviceman = await db.get(IpptServiceman, serviceman_id)
    if serviceman is None:
        return None
    serviceman.excluded = excluded
    serviceman.exclusion_reason = reason if excluded else None
    serviceman.updated_at = utc_dt.ensure_naive(utc_dt.utcnow())
    await db.flush()
    return serviceman


async def excluded_servicemen(db: AsyncSession) -> list[dict]:
    """Rows removed from tracking, for the upload page's review panel."""
    rows = (
        (
            await db.execute(
                select(IpptServiceman)
                .where(IpptServiceman.excluded.is_(True))
                .order_by(IpptServiceman.full_name)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "serviceman_id": serviceman.id,
            "rank": serviceman.rank,
            "full_name": serviceman.full_name,
            "exclusion_reason": serviceman.exclusion_reason,
        }
        for serviceman in rows
    ]


async def excluded_count(db: AsyncSession) -> int:
    """How many spine rows are currently removed from tracking."""
    return (
        await db.scalar(
            select(func.count())
            .select_from(IpptServiceman)
            .where(IpptServiceman.excluded.is_(True))
        )
    ) or 0


async def window_detail(db: AsyncSession, personnel_id: str) -> dict | None:
    """Per-personnel window view (§3.3 trajectory): latest state, the
    full observation history oldest → newest, screening history, and the
    linked roll row when matched. ``None`` when the id is unknown or the
    person has no IPPT linkage."""
    try:
        personnel = await db.get(Personnel, personnel_id)
    except Exception:  # noqa: BLE001 — non-UUID ids simply don't resolve
        return None
    servicemen = (
        (
            await db.execute(
                select(IpptServiceman).where(
                    IpptServiceman.personnel_id == personnel_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not servicemen:
        return None
    serviceman_ids = [serviceman.id for serviceman in servicemen]

    observations = (
        await db.execute(
            select(IpptStateObservation, IpptSnapshot.report_date, IpptWindow)
            .join(IpptSnapshot, IpptStateObservation.snapshot_id == IpptSnapshot.id)
            .outerjoin(IpptWindow, IpptStateObservation.window_id == IpptWindow.id)
            .where(IpptStateObservation.serviceman_id.in_(serviceman_ids))
            .order_by(IpptSnapshot.report_date, IpptStateObservation.id)
        )
    ).all()
    if not observations:
        return None

    trajectory = []
    latest_observation: IpptStateObservation | None = None
    for observation, report_date, window in observations:
        latest_observation = observation  # rows arrive oldest → newest
        trajectory.append(
            {
                "report_date": report_date.isoformat(),
                "state": observation.state,
                "state_label": STATE_LABELS[observation.state],
                "ippt_status": observation.ippt_status,
                "fit_sessions": observation.fit_sessions,
                "booked": observation.booked,
                "ffi": observation.ffi,
                "window_end": (
                    window.window_end.isoformat() if window is not None else None
                ),
            }
        )

    screenings = (
        (
            await db.execute(
                select(IpptHealthScreening)
                .where(IpptHealthScreening.serviceman_id.in_(serviceman_ids))
                .order_by(IpptHealthScreening.observed_on)
            )
        )
        .scalars()
        .all()
    )

    # Latest state via the shared dashboard computation, filtered down.
    states = await latest_states(db)
    entry = next(
        (state for state in states if state.serviceman_id in serviceman_ids), None
    )

    serviceman = servicemen[0]
    # Display identity follows the latest observation, like the dashboard
    # (item 2 of the 2026-10-08 hardening); the spine row additionally
    # carries the tracking-exclusion state (item 1).
    identity = latest_observation if latest_observation is not None else serviceman
    return {
        "serviceman": {
            "id": serviceman.id,
            "rank": identity.rank,
            "full_name": serviceman.full_name,
            "unit": serviceman.unit,
            "sub_unit": identity.sub_unit,
            "match_status": serviceman.match_status,
            "excluded": serviceman.excluded,
            "exclusion_reason": serviceman.exclusion_reason,
        },
        "personnel": (
            {
                "id": str(personnel.id),
                "rank": personnel.rank,
                "full_name": personnel.full_name,
                "sub_unit_1": personnel.sub_unit_1,
            }
            if personnel is not None
            else None
        ),
        "latest": entry.to_dict() if entry else None,
        "trajectory": trajectory,
        "screenings": [
            {
                "observed_on": screening.observed_on.isoformat(),
                "status": screening.status,
                "screened_on": (
                    screening.screened_on.isoformat() if screening.screened_on else None
                ),
            }
            for screening in screenings
        ],
    }


# ============================================================================
# helpers
# ============================================================================


def _display_order(entry: ServicemanState) -> tuple:
    """Highlighted-first, most-urgent-first, then roster order."""
    urgency = entry.days_to_close if entry.days_to_close is not None else 10_000
    return (urgency, entry.full_name)


def _count_by(entries: list[ServicemanState], key) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[key(entry)] = counts.get(key(entry), 0) + 1
    return counts


async def report_date_count(db: AsyncSession) -> int:
    """Number of distinct report dates ingested."""
    return (
        await db.scalar(select(func.count(func.distinct(IpptSnapshot.report_date))))
    ) or 0


async def latest_report_date(db: AsyncSession) -> str | None:
    """Report date of the most recently ingested dataset (the IPPT
    analogue of a nominal roll's CAA), as an ISO string; ``None`` before
    the first upload."""
    latest = await db.scalar(select(func.max(IpptSnapshot.report_date)))
    return latest.isoformat() if latest is not None else None
