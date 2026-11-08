"""Defaulter-report service (docs/IPPT_MONITORING.md §7; FEATURE_IPPT).

One ingest = one quarter = one file (CSV or first-tab XLSX), atomic per
quarter. Uploading a quarter that is already current replaces it
(replace-on-re-ingest, like the IPPT snapshots — reason/remarks
annotations on the discarded rows go with them); uploading any other
quarter archives the previously current one, keeping its rows for the
future pending↔archived validation (deferred, 2026-11-08). An
already-archived quarter is rejected: history is not rewritten.

Personnel resolution: ``PERS`` first (unique pers_no hit on the
attendance-active roll), falling back to the normalized full name (the
IPPT §3.2 contract: unique → matched, homonyms → ambiguous, miss →
unmatched). Matched rows denormalize the effective sub-unit 1 at ingest
for display — the file's own UNIT is the whole unit, not a subunit.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import (
    DEFAULTER_REASONS,
    DefaulterQuarantinedRow,
    DefaulterReport,
    DefaulterRow,
    NominalRoll,
    Personnel,
    Tagging,
    TaggingEntry,
)
from parade_state.utils import utc_dt
from parade_state.utils.defaulter_reports import (
    QuarterError,
    parse_defaulter_file,
    validate_quarter,
)
from parade_state.utils.ippt_reports import ReportFileError, normalize_name

logger = logging.getLogger(__name__)


class DefaulterIngestError(ValueError):
    """The submission is rejected whole (bad quarter, unusable file, or
    an archived quarter being re-uploaded) — nothing lands."""


class DefaulterRowError(ValueError):
    """The row edit is rejected (archived quarter, bad reason)."""


class DefaulterRowNotFound(LookupError):
    """No defaulter row with the given id."""


class DefaulterRowArchived(DefaulterRowError):
    """The row belongs to an archived (read-only) quarter."""


@dataclass
class DefaulterIngestResult:
    year: int
    quarter: int
    report_id: int
    replaced: bool
    archived_labels: list[str] = field(default_factory=list)
    pending_rows: int = 0
    archived_rows: int = 0
    quarantined_total: int = 0
    match_counts: dict[str, int] = field(
        default_factory=lambda: {"matched": 0, "unmatched": 0, "ambiguous": 0}
    )
    unmatched_names: list[str] = field(default_factory=list)
    ambiguous_names: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"Q{self.quarter} {self.year}"

    @property
    def total_rows(self) -> int:
        return self.pending_rows + self.archived_rows


# ============================================================================
# Ingest
# ============================================================================


async def ingest_defaulter_report(
    db: AsyncSession,
    filename: str,
    content: bytes,
    year: int,
    quarter: int,
) -> DefaulterIngestResult:
    """Ingest one quarterly defaulter release, atomically.

    Raises:
        DefaulterIngestError: bad quarter label, unusable file, or the
            quarter was already archived. Nothing is written.
    """
    try:
        year, quarter = validate_quarter(year, quarter)
    except QuarterError as exc:
        raise DefaulterIngestError(str(exc)) from exc
    try:
        parsed = parse_defaulter_file(filename, content)
    except ReportFileError as exc:
        raise DefaulterIngestError(str(exc)) from exc

    existing = (
        await db.execute(
            select(DefaulterReport).where(
                DefaulterReport.year == year, DefaulterReport.quarter == quarter
            )
        )
    ).scalar_one_or_none()

    replaced = False
    archived_labels: list[str] = []
    if existing is not None and existing.status == "archived":
        raise DefaulterIngestError(
            f"{existing.label} was already archived — history is not "
            "rewritten. Upload the new quarter instead."
        )
    if existing is not None:
        # Replace-on-re-ingest of the current quarter; explicit child
        # deletes keep SQLite (no FK enforcement) consistent.
        replaced = True
        await db.execute(
            delete(DefaulterQuarantinedRow).where(
                DefaulterQuarantinedRow.report_id == existing.id
            )
        )
        await db.execute(
            delete(DefaulterRow).where(DefaulterRow.report_id == existing.id)
        )
        await db.execute(
            delete(DefaulterReport).where(DefaulterReport.id == existing.id)
        )
    else:
        # Any other quarter supersedes the previously current one.
        current = (
            (
                await db.execute(
                    select(DefaulterReport).where(DefaulterReport.status == "current")
                )
            )
            .scalars()
            .all()
        )
        if current:
            await db.execute(
                update(DefaulterReport)
                .where(DefaulterReport.status == "current")
                .values(status="archived")
            )
            archived_labels = [report.label for report in current]

    report = DefaulterReport(
        year=year,
        quarter=quarter,
        status="current",
        filename=filename,
        row_count=len(parsed.rows),
        ingested_at=utc_dt.ensure_naive(utc_dt.utcnow()),
    )
    db.add(report)
    await db.flush()

    roster = await _load_active_roster(db)
    pending_rows = archived_rows = 0
    quarantined_total = 0
    match_counts = {"matched": 0, "unmatched": 0, "ambiguous": 0}
    unmatched_names: list[str] = []
    ambiguous_names: list[str] = []
    matched_ids: set[str] = set()

    for entry in parsed.quarantined:
        db.add(
            DefaulterQuarantinedRow(
                report_id=report.id,
                line_number=entry.line_number,
                raw_row=entry.raw_row,
                reason=entry.reason,
            )
        )
        quarantined_total += 1

    for row in parsed.rows:
        personnel_id, match_status = _resolve_personnel(roster, row)
        if match_status is not None:
            match_counts[match_status] += 1
            if match_status == "unmatched":
                unmatched_names.append(row.full_name)
            elif match_status == "ambiguous":
                ambiguous_names.append(row.full_name)
        if personnel_id is not None:
            matched_ids.add(personnel_id)
        if row.row_type == "pending":
            pending_rows += 1
        else:
            archived_rows += 1
        db.add(
            DefaulterRow(
                report_id=report.id,
                row_type=row.row_type,
                unit=row.unit,
                pers_no=row.pers_no,
                rank=row.rank,
                full_name=row.full_name,
                full_name_norm=row.full_name_norm,
                personnel_id=personnel_id,
                match_status=match_status,
                sub_unit=roster.sub_units.get(personnel_id)
                if personnel_id is not None
                else None,
                offence_type=row.offence_type,
                offence_window=row.offence_window,
                default_count=row.default_count,
                defaulted_count=row.defaulted_count,
            )
        )

    result = DefaulterIngestResult(
        year=year,
        quarter=quarter,
        report_id=report.id,
        replaced=replaced,
        archived_labels=archived_labels,
        pending_rows=pending_rows,
        archived_rows=archived_rows,
        quarantined_total=quarantined_total,
        match_counts=match_counts,
        unmatched_names=unmatched_names,
        ambiguous_names=ambiguous_names,
    )
    logger.info(
        "Defaulter report %s ingested: %d rows (%d pending / %d archived), "
        "%d quarantined, matched %d / unmatched %d / ambiguous %d%s",
        result.label,
        result.total_rows,
        pending_rows,
        archived_rows,
        quarantined_total,
        match_counts["matched"],
        match_counts["unmatched"],
        match_counts["ambiguous"],
        " (replaced previous ingest)" if replaced else "",
    )
    return result


class _Roster:
    """The attendance-active roll in memory: name/pers indexes plus the
    effective sub-unit 1 of every personnel (tagging overlay applied)."""

    def __init__(self) -> None:
        self.by_norm: dict[str, list[Personnel]] = {}
        self.by_pers: dict[str, list[Personnel]] = {}
        self.sub_units: dict[str, str | None] = {}


async def _load_active_roster(db: AsyncSession) -> _Roster:
    """Load the active roll once per ingest (rows number in the dozens;
    the per-row query pattern of the IPPT ingest is avoided instead of
    copied)."""
    roster = _Roster()
    active_roll_id = (
        await db.execute(
            select(NominalRoll.id).where(NominalRoll.attendance_active.is_(True))
        )
    ).scalar_one_or_none()
    if active_roll_id is None:
        return roster

    people = (
        (
            await db.execute(
                select(Personnel).where(
                    Personnel.nominal_roll_id == str(active_roll_id),
                    Personnel.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    for person in people:
        roster.by_norm.setdefault(normalize_name(person.full_name), []).append(person)
        if person.pers_no:
            roster.by_pers.setdefault(person.pers_no, []).append(person)

    # Effective sub-unit 1: the active roll's tagging overlay, entry
    # verbatim (None clears), canonical otherwise.
    tagging = (
        await db.execute(
            select(Tagging).where(Tagging.nominal_roll_id == str(active_roll_id))
        )
    ).scalar_one_or_none()
    overrides: dict[str, str | None] = {}
    if tagging is not None:
        entries = (
            (
                await db.execute(
                    select(TaggingEntry).where(
                        TaggingEntry.tagging_id == str(tagging.id)
                    )
                )
            )
            .scalars()
            .all()
        )
        overrides = {str(entry.personnel_id): entry.to_sub_unit_1 for entry in entries}
    roster.sub_units = {
        str(person.id): overrides.get(str(person.id), person.sub_unit_1)
        for person in people
    }
    return roster


def _resolve_personnel(roster: _Roster, row) -> tuple[str | None, str | None]:
    """Match one row to the active roll: PERS first, then the
    normalized name. Returns ``(personnel_id, match_status)``; an empty
    roster (no active roll) simply misses → unmatched, like the IPPT
    ingest."""

    def verdict(candidates: list[Personnel]) -> tuple[str | None, str]:
        if len(candidates) == 1:
            return str(candidates[0].id), "matched"
        return None, "ambiguous"

    if row.pers_no and roster.by_pers.get(row.pers_no):
        # A pers_no hit decides; no hit falls through to the name.
        return verdict(roster.by_pers[row.pers_no])
    candidates = roster.by_norm.get(row.full_name_norm, [])
    if len(candidates) == 1:
        return str(candidates[0].id), "matched"
    if len(candidates) > 1:
        return None, "ambiguous"
    return None, "unmatched"


# ============================================================================
# Reads + row edits
# ============================================================================


async def current_defaulters(db: AsyncSession) -> dict | None:
    """The current quarter's display payload: the report header plus its
    Pending rows ordered for the dashboard table (subunit, rank, name).
    ``None`` = nothing ingested yet."""
    report = (
        await db.execute(
            select(DefaulterReport).where(DefaulterReport.status == "current")
        )
    ).scalar_one_or_none()
    if report is None:
        return None

    rows = (
        (
            await db.execute(
                select(DefaulterRow)
                .where(
                    DefaulterRow.report_id == report.id,
                    DefaulterRow.row_type == "pending",
                )
                .order_by(
                    DefaulterRow.sub_unit, DefaulterRow.rank, DefaulterRow.full_name
                )
            )
        )
        .scalars()
        .all()
    )
    archived_count = await db.scalar(
        select(func.count())
        .select_from(DefaulterRow)
        .where(
            DefaulterRow.report_id == report.id,
            DefaulterRow.row_type == "archived",
        )
    )
    quarantined_count = await db.scalar(
        select(func.count())
        .select_from(DefaulterQuarantinedRow)
        .where(DefaulterQuarantinedRow.report_id == report.id)
    )
    return {
        "report": {
            "id": report.id,
            "label": report.label,
            "year": report.year,
            "quarter": report.quarter,
            "filename": report.filename,
            "ingested_at": utc_dt.ensure_naive(report.ingested_at).isoformat(
                sep=" ", timespec="minutes"
            ),
            "pending_rows": len(rows),
            "archived_rows": archived_count or 0,
            "quarantined": quarantined_count or 0,
        },
        "rows": [
            {
                "id": row.id,
                "rank": row.rank,
                "full_name": row.full_name,
                "sub_unit": row.sub_unit,
                "match_status": row.match_status,
                "offence_type": row.offence_type,
                "offence_window": row.offence_window,
                "defaulted_count": row.defaulted_count,
                "reason": row.reason,
                "remarks": row.remarks,
            }
            for row in rows
        ],
    }


async def update_defaulter_row(
    db: AsyncSession,
    row_id: int,
    reason: str | None,
    remarks: str | None,
) -> DefaulterRow:
    """Set one row's ``reason`` and/or ``remarks`` (audit-logged by the
    API layer).

    ``None`` leaves the field unchanged; an empty string clears it
    (stored NULL). Only rows of the current quarter are editable — the
    archived quarters are read-only history.

    Raises:
        DefaulterRowNotFound: unknown row id.
        DefaulterRowArchived: the row's quarter is archived.
        DefaulterRowError: a reason outside the enum.
    """
    row = await db.get(DefaulterRow, row_id)
    if row is None:
        raise DefaulterRowNotFound(f"No defaulter row {row_id}")
    report = await db.get(DefaulterReport, row.report_id)
    if report is None or report.status != "current":
        raise DefaulterRowArchived(
            "This row belongs to an archived quarter — archived data is read-only"
        )
    if reason is not None and reason != "" and reason not in DEFAULTER_REASONS:
        raise DefaulterRowError(
            "Reason must be one of: " + ", ".join(DEFAULTER_REASONS)
        )
    if reason is not None:
        row.reason = reason or None
    if remarks is not None:
        row.remarks = remarks.strip() or None
    await db.flush()
    return row


async def defaulter_history(db: AsyncSession) -> list[dict]:
    """Every ingested quarter, newest first, with its row counts."""
    reports = (
        (
            await db.execute(
                select(DefaulterReport).order_by(
                    DefaulterReport.year.desc(), DefaulterReport.quarter.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    out: list[dict] = []
    for report in reports:
        pending = await db.scalar(
            select(DefaulterRow.id).where(
                DefaulterRow.report_id == report.id,
                DefaulterRow.row_type == "pending",
            )
        )
        archived = await db.scalar(
            select(DefaulterRow.id).where(
                DefaulterRow.report_id == report.id,
                DefaulterRow.row_type == "archived",
            )
        )
        quarantined = await db.scalar(
            select(DefaulterQuarantinedRow.id).where(
                DefaulterQuarantinedRow.report_id == report.id
            )
        )
        out.append(
            {
                "id": report.id,
                "label": report.label,
                "status": report.status,
                "filename": report.filename,
                "pending_rows": pending or 0,
                "archived_rows": archived or 0,
                "quarantined": quarantined or 0,
                "ingested_at": utc_dt.ensure_naive(report.ingested_at).isoformat(
                    sep=" ", timespec="minutes"
                ),
            }
        )
    return out


async def defaulter_quarantined(db: AsyncSession) -> list[dict]:
    """Quarantined defaulter rows across all quarters, newest quarter
    first — the review list for the upload page."""
    entries = (
        await db.execute(
            select(DefaulterQuarantinedRow, DefaulterReport)
            .join(
                DefaulterReport,
                DefaulterQuarantinedRow.report_id == DefaulterReport.id,
            )
            .order_by(
                DefaulterReport.year.desc(),
                DefaulterReport.quarter.desc(),
                DefaulterQuarantinedRow.line_number,
            )
        )
    ).all()
    return [
        {
            "id": entry.id,
            "label": report.label,
            "line_number": entry.line_number,
            "raw_row": entry.raw_row,
            "reason": entry.reason,
        }
        for entry, report in entries
    ]
