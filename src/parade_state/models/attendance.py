"""Attendance models.

Attendance is taken once daily against the Nominal Roll that is currently
**active for attendance** — always with the NR's 1:1 Tagging overlay
applied. One ``Attendance`` row per ``(personnel, date)`` carries a
``status`` (present/absent), an optional ``reason`` classifying the
remarks, and free-text ``remarks``.

A super-admin marks an NR "Use for Attendance" (``NominalRoll.attendance_active``);
writes are only permitted against that NR. There is no separate scope table.
A super-admin may additionally freeze a day (``AttendanceFreeze``): frozen
(NR, date) attendance stays super-admin-writable and becomes read-only for
admins.
"""

from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .csv_ingestion import NominalRoll
    from .personnel import Personnel

# Canonical attendance status values (stored lowercase snake_case).
ATTENDANCE_STATUSES: tuple[str, ...] = (
    "present",
    "absent",
)

# Optional reason classifying an attendance row's remarks. Never feeds
# present/absent aggregation (issue 33) — strength reporting relies on
# status only.
ATTENDANCE_REASONS: tuple[str, ...] = (
    "mc",
    "off",
    "early_outpro",
    "other",
    "awol",
)

ATTENDANCE_REASON_LABELS: dict[str, str] = dict(
    mc="MC",
    off="Off",
    early_outpro="Early Outpro",
    other="Other",
    awol="AWOL",
)

# Statuses counted as "present" when aggregating into present/absent buckets.
# Everything not in this set counts as absent.
PRESENT_LIKE_STATUSES: frozenset[str] = frozenset({"present"})


class Attendance(Base):
    """Per-personnel per-day attendance: status + optional reason + remarks.

    ``nominal_roll_id`` is always the parent NR of the personnel row (the
    active NR at write time). The unique constraint enforces one record per
    person per day.
    """

    __tablename__ = "attendance"

    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE"), index=True
    )
    nominal_roll_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nominal_rolls.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[utc_dt.date] = mapped_column(Date, index=True)

    status: Mapped[str] = mapped_column(
        Enum(*ATTENDANCE_STATUSES, name="attendance_status"),
        default="absent",
    )
    reason: Mapped[str | None] = mapped_column(
        Enum(*ATTENDANCE_REASONS, name="attendance_reason"),
        nullable=True,
    )
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Roster snapshot at the time the record was created.
    notes_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_1_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_2_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_3_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Audit trail.
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    updated_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    last_edit_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    last_edit_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    is_retroactive_edit: Mapped[bool] = mapped_column(default=False)

    # Relationships
    personnel: Mapped["Personnel"] = relationship(back_populates="attendance")
    nominal_roll: Mapped["NominalRoll"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "personnel_id", "date", name="uq_attendance_personnel_date"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Attendance(personnel_id={self.personnel_id!r}, "
            f"date={self.date!r}, status={self.status!r}, "
            f"reason={self.reason!r})>"
        )


class AttendanceFreeze(Base):
    """Day-level attendance lock for a nominal roll (issue 35).

    Row presence = the (NR, date) is frozen: admins get read-only cells
    and 403s on writes, super-admins keep editing (retro-edit provenance
    still applies). Unfreezing deletes the row. The freeze cascades with
    the NR, so purged rolls take their freezes along.
    """

    __tablename__ = "attendance_freezes"

    nominal_roll_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nominal_rolls.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[utc_dt.date] = mapped_column(Date, index=True)

    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))

    nominal_roll: Mapped["NominalRoll"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "nominal_roll_id", "date", name="uq_attendance_freezes_nr_date"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AttendanceFreeze(nominal_roll_id={self.nominal_roll_id!r}, "
            f"date={self.date!r})>"
        )
