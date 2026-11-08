"""Defaulter-report models (docs/IPPT_MONITORING.md §7).

The unit's quarterly defaulter release (CSV or the first tab of an
XLSX) ingests as one batch per quarter: ``defaulter_reports`` is the
batch (year + calendar quarter, current vs archived status) and
``defaulter_rows`` are its rows. Rows the file itself marks
``TYPE=Archived`` ingest but never display — they are this quarter's
own concluded cases, kept so a later session can validate them against
last quarter's Pending rows (deferred decision, 2026-11-08).

Two annotation columns the dashboard edits in place — ``reason`` and
``remarks`` — are per-row app data the source file never carries; they
do not survive a replace-on-re-ingest of the same quarter (the rows
they annotate are dropped with it).

Dialect notes follow models/ippt.py: BigInteger PKs variant to plain
integers on SQLite; ``personnel_id`` is ON DELETE SET NULL because
replacing the active nominal roll must not block, and the link simply
falls off (a future re-ingest re-resolves it).
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from parade_state.utils import utc_dt

from ..db import Base

# Which rows a quarter's file carries: ``pending`` = open cases (the
# dashboard table), ``archived`` = the file's own concluded cases.
ROW_TYPES: tuple[str, ...] = ("pending", "archived")

# Batch lifecycle: exactly one ``current`` report at a time; ingesting a
# different quarter archives the previous one.
REPORT_STATUSES: tuple[str, ...] = ("current", "archived")

# Why the person defaulted — dashboard-selectable, stored lowercased.
DEFAULTER_REASONS: tuple[str, ...] = (
    "hsp",
    "attitude_issue",
    "uncontactable",
    "others",
)

REASON_LABELS: dict[str, str] = {
    "hsp": "HSP",
    "attitude_issue": "Attitude Issue",
    "uncontactable": "Uncontactable",
    "others": "Others",
}


class DefaulterReport(Base):
    """One ingested quarterly release (the batch + its provenance).

    ``unique (year, quarter)``: re-uploading the current quarter
    replaces it in place; an already-archived quarter stays put (the
    service rejects the re-upload — history is not rewritten).

    Calendar quarters (Q1 = Jan 1 – Mar 31) even though the workyear
    runs Apr 1 – Mar 31: the quarter tag is only a label the user picks
    at upload, never derived from the data.
    """

    __tablename__ = "defaulter_reports"
    __table_args__ = (
        CheckConstraint("quarter IN (1, 2, 3, 4)", name="ck_defaulter_reports_quarter"),
        CheckConstraint(
            "status IN ('current', 'archived')", name="ck_defaulter_reports_status"
        ),
        UniqueConstraint("year", "quarter", name="uq_defaulter_report_year_quarter"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="current", index=True)
    filename: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer)
    ingested_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )

    @property
    def label(self) -> str:
        return f"Q{self.quarter} {self.year}"

    def __repr__(self) -> str:
        return (
            f"<DefaulterReport(id={self.id!r}, label={self.label!r}, "
            f"status={self.status!r})>"
        )


class DefaulterRow(Base):
    """One defaulter line of one quarterly batch.

    ``defaulted_count`` is the "number of times defaulted" — the
    ``SAF264 ISSUED`` column of Pending sections, ``DEFAULTER COUNT`` of
    Archived ones (same semantic slot, section-dependent header);
    ``default_count`` is the file's other count and is stored but not
    displayed. ``sub_unit`` is the effective sub-unit 1 of the matched
    personnel at ingest time (the file's own UNIT is the whole unit) and
    is denormalized for display, like the IPPT observations'.
    """

    __tablename__ = "defaulter_rows"
    __table_args__ = (
        CheckConstraint(
            "row_type IN ('pending', 'archived')", name="ck_defaulter_row_type"
        ),
        CheckConstraint(
            "reason IS NULL OR reason IN ('hsp', 'attitude_issue',"
            " 'uncontactable', 'others')",
            name="ck_defaulter_row_reason",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    report_id: Mapped[int] = mapped_column(
        ForeignKey("defaulter_reports.id", ondelete="CASCADE"), index=True
    )
    row_type: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str] = mapped_column(String(255))
    pers_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rank: Mapped[str] = mapped_column(String(16))
    full_name: Mapped[str] = mapped_column(Text)
    full_name_norm: Mapped[str] = mapped_column(String(255), index=True)
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
            name="ck_defaulter_row_match_status",
        ),
        nullable=True,
    )
    sub_unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    offence_type: Mapped[str] = mapped_column(String(64))
    offence_window: Mapped[str] = mapped_column(String(64))
    default_count: Mapped[int] = mapped_column(Integer)
    defaulted_count: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DefaulterRow(id={self.id!r}, full_name={self.full_name!r}, "
            f"row_type={self.row_type!r})>"
        )


class DefaulterQuarantinedRow(Base):
    """Row-level validation rejects of one batch, kept for review —
    same contract as IpptQuarantinedRow: a bad row never takes the
    quarter down with it, nor vanishes without a trace."""

    __tablename__ = "defaulter_quarantined_rows"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    report_id: Mapped[int] = mapped_column(
        ForeignKey("defaulter_reports.id", ondelete="CASCADE"), index=True
    )
    line_number: Mapped[int] = mapped_column(Integer)
    raw_row: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)

    def __repr__(self) -> str:
        return (
            f"<DefaulterQuarantinedRow(id={self.id!r}, "
            f"line_number={self.line_number!r})>"
        )
