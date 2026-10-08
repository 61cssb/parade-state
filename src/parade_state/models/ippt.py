"""IPPT monitoring models (docs/IPPT_MONITORING.md §6).

The six monitoring reports are projections of one underlying state
machine, so the schema stores what the source actually provides —
periodic state observations — rather than inventing event-level history
the reports don't contain (no per-attempt results, no per-session
dates, §6.5). One ingest = one report date = the full six-file snapshot,
atomic per report date (§4).

These tables are part of the IPPT feature, gated behind `FEATURE_IPPT`
(local runs and the Railway development environment opt in; the Railway
production environment force-disables the flag, see config.Settings).

Dialect notes: primary keys are bigserial on PostgreSQL and plain
integer rowids on SQLite (``BigInteger().with_variant(Integer,
"sqlite")``). ``ippt_servicemen.personnel_id`` is ON DELETE SET NULL:
replacing the active nominal roll deletes its Personnel rows, and the
link must simply fall off (re-resolvable at the next ingest by §3.2
rule 2) rather than block the delete.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from parade_state.utils import utc_dt

from ..db import Base

# The six report files (docs/IPPT_MONITORING.md §2.1). One snapshot =
# all six for one report date.
FILE_KINDS: tuple[str, ...] = (
    "ippt_completed",
    "ippt_failed",
    "ippt_not_attempted",
    "nsfit_completed",
    "nsfit_in_progress",
    "nsfit_not_started",
)

# Unified obligation states derived at ingest (§6.2). Completion is set
# only from these designations — never by counting FIT sessions (§2.3).
OBSERVATION_STATES: tuple[str, ...] = (
    "met_award",
    "met_voluntary_fit",
    "met_mandatory_fit",
    "waived",
    "failed_can_reattempt",
    "voluntary_fit_in_progress",
    "mandatory_fit_in_progress",
    "mandatory_fit_failed_attempt",
    "ippt_not_attempted",
    "nsfit_not_started",
)

# FFI health-screening statuses (§2.3). 'pending' blocks IPPT booking
# until cleared; status <> 'na' means the 35+ screening applies.
FFI_STATUSES: tuple[str, ...] = ("na", "fit", "pending")

# §3.2 identity-resolution outcome, display-only for now: matched =
# unique normalized-name hit on the attendance-active roll, unmatched =
# no hit, ambiguous = homonyms (flagged for manual review).
MATCH_STATUSES: tuple[str, ...] = ("matched", "unmatched", "ambiguous")

STATE_LABELS: dict[str, str] = {
    "met_award": "Met — IPPT award",
    "met_voluntary_fit": "Met — voluntary NS FIT",
    "met_mandatory_fit": "Met — mandatory NS FIT discharged",
    "waived": "Waived — special handling",
    "failed_can_reattempt": "Attempted — failed (can re-attempt)",
    "voluntary_fit_in_progress": "Voluntary NS FIT in progress",
    "mandatory_fit_in_progress": "Mandatory NS FIT in progress",
    "mandatory_fit_failed_attempt": "Mandatory NS FIT + failed IPPT attempt",
    "ippt_not_attempted": "Not attempted",
    "nsfit_not_started": "Mandatory NS FIT — not started",
}


class IpptServiceman(Base):
    """Identity spine: one row per serviceman seen in any report (§6.1).

    No unique natural key: rank/sub-unit drift and homonyms make
    (name, sub-unit) a match *hint*, not a constraint. ``full_name_norm``
    (uppercased, whitespace-collapsed) keys the idempotent re-ingest
    lookup and the personnel matching of §3.2.

    ``excluded`` removes a serviceman from the dashboard/tier views
    (remove-from-tracking, decided 2026-10-08): rows are retained, the
    exclusion is audit-logged, and a later snapshot that shows the person
    again surfaces them in the upload response for re-inclusion — it
    never lifts the exclusion by itself.
    """

    __tablename__ = "ippt_servicemen"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    rank: Mapped[str] = mapped_column(String(16))
    full_name: Mapped[str] = mapped_column(Text)
    full_name_norm: Mapped[str] = mapped_column(String(255), index=True)
    unit: Mapped[str] = mapped_column(String(32))
    sub_unit: Mapped[str] = mapped_column(String(64))
    personnel_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("personnel.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    match_status: Mapped[str | None] = mapped_column(
        String(16),
        CheckConstraint(
            "match_status IN ('matched', 'unmatched', 'ambiguous')",
            name="ck_ippt_serviceman_match_status",
        ),
        nullable=True,
    )
    excluded: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    updated_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )

    def __repr__(self) -> str:
        return f"<IpptServiceman(id={self.id!r}, full_name={self.full_name!r})>"


class IpptSnapshot(Base):
    """One ingested report file: provenance + the atomicity anchor (§6.1).

    ``unique (report_date, file_kind)`` keeps one authoritative copy per
    date; re-uploading a report date replaces the previous ingest.
    """

    __tablename__ = "ippt_snapshots"
    __table_args__ = (
        CheckConstraint(
            "file_kind IN ('ippt_completed', 'ippt_failed', 'ippt_not_attempted',"
            " 'nsfit_completed', 'nsfit_in_progress', 'nsfit_not_started')",
            name="ck_ippt_snapshots_file_kind",
        ),
        UniqueConstraint("report_date", "file_kind", name="uq_ippt_snapshot_date_kind"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    report_date: Mapped[utc_dt.date] = mapped_column(Date, index=True)
    file_kind: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer)
    ingested_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )

    def __repr__(self) -> str:
        return (
            f"<IpptSnapshot(id={self.id!r}, report_date={self.report_date!r}, "
            f"file_kind={self.file_kind!r})>"
        )


class IpptWindow(Base):
    """Birthday-anchored IPPT window per serviceman, derived at ingest.

    ``window_end`` = report_date + window_close days from an outstanding
    report row; ``window_start`` = (window_end + 1 day) minus one year
    (the birthday). A change of window_end across snapshots = window
    rollover, which is how FAILED → mandatory-NS-FIT transitions are
    recognised (§6.1).
    """

    __tablename__ = "ippt_windows"
    __table_args__ = (
        UniqueConstraint("serviceman_id", "window_end", name="uq_ippt_window_end"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    serviceman_id: Mapped[int] = mapped_column(
        ForeignKey("ippt_servicemen.id", ondelete="CASCADE"), index=True
    )
    window_end: Mapped[utc_dt.date] = mapped_column(Date)
    window_start: Mapped[utc_dt.date] = mapped_column(Date)

    def __repr__(self) -> str:
        return (
            f"<IpptWindow(id={self.id!r}, serviceman_id={self.serviceman_id!r}, "
            f"window_end={self.window_end!r})>"
        )


class IpptStateObservation(Base):
    """The trajectory spine: one row per serviceman per snapshot (§6.1).

    ``unique (snapshot_id, serviceman_id)`` also enforces the at-most-
    one-file rule (§2.1). ``window_id`` is NULL for ``*_COMPLETED`` rows
    (they carry no window close); ingest backfills it from the
    serviceman's preceding outstanding observation when one exists.
    """

    __tablename__ = "ippt_state_observations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('met_award', 'met_voluntary_fit', 'met_mandatory_fit',"
            " 'waived', 'failed_can_reattempt', 'voluntary_fit_in_progress',"
            " 'mandatory_fit_in_progress', 'mandatory_fit_failed_attempt',"
            " 'ippt_not_attempted', 'nsfit_not_started')",
            name="ck_ippt_observation_state",
        ),
        CheckConstraint(
            "ffi IS NULL OR ffi IN ('na', 'fit', 'pending')",
            name="ck_ippt_observation_ffi",
        ),
        UniqueConstraint(
            "snapshot_id",
            "serviceman_id",
            name="uq_ippt_observation_snapshot_serviceman",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("ippt_snapshots.id", ondelete="CASCADE"), index=True
    )
    serviceman_id: Mapped[int] = mapped_column(
        ForeignKey("ippt_servicemen.id", ondelete="CASCADE"), index=True
    )
    window_id: Mapped[int | None] = mapped_column(
        ForeignKey("ippt_windows.id", ondelete="SET NULL"), nullable=True
    )
    rank: Mapped[str] = mapped_column(String(16))
    sub_unit: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(48))
    ippt_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fit_sessions: Mapped[int | None] = mapped_column(nullable=True)
    window_close_days: Mapped[int | None] = mapped_column(nullable=True)
    booked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ffi: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reminder_sent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sent: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<IpptStateObservation(id={self.id!r}, snapshot_id={self.snapshot_id!r}, "
            f"serviceman_id={self.serviceman_id!r}, state={self.state!r})>"
        )


class IpptHealthScreening(Base):
    """FFI health-screening history, mirroring observations.ffi (§6.1).

    ``screened_on`` is approximated at ingest as the report_date of the
    first snapshot showing 'fit' after a 'pending'; NULL until such a
    flip is observed.
    """

    __tablename__ = "ippt_health_screenings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('na', 'fit', 'pending')", name="ck_ippt_screening_status"
        ),
        UniqueConstraint(
            "serviceman_id", "observed_on", name="uq_ippt_screening_serviceman_date"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    serviceman_id: Mapped[int] = mapped_column(
        ForeignKey("ippt_servicemen.id", ondelete="CASCADE"), index=True
    )
    observed_on: Mapped[utc_dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    screened_on: Mapped[utc_dt.date | None] = mapped_column(Date, nullable=True)


class IpptQuarantinedRow(Base):
    """Row-level validation rejects, kept for review (§4).

    A bad row must not take the file (or the report date) down with it,
    nor vanish without a trace: it lands here with its raw line and the
    rejection reason while the rest of the file ingests.
    """

    __tablename__ = "ippt_quarantined_rows"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("ippt_snapshots.id", ondelete="CASCADE"), index=True
    )
    line_number: Mapped[int] = mapped_column(Integer)
    raw_row: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
