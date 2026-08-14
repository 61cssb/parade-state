"""Attendance models.

Attendance is taken twice daily (AM/PM, hardcoded) against the Nominal Roll
that is currently **active for attendance** — always with the NR's 1:1
Tagging overlay applied. One ``Attendance`` row per ``(personnel, date)``
carries status + remarks for both AM and PM slots.

A super-admin marks an NR "Use for Attendance" (``NominalRoll.attendance_active``);
writes are only permitted against that NR. There is no separate scope table.
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
    "time_off",
    "mc",
    "yet_to_inpro",
    "outpro",
    "reporting_sick",
    "late",
    "att_out",
)

# Statuses counted as "present" when aggregating into present/absent buckets.
# Everything not in this set counts as absent.
PRESENT_LIKE_STATUSES: frozenset[str] = frozenset({"present", "late"})


class Attendance(Base):
    """Per-personnel per-day attendance, carrying AM and PM status + remarks.

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

    status_am: Mapped[str] = mapped_column(
        Enum(*ATTENDANCE_STATUSES, name="attendance_status"),
        default="absent",
    )
    remarks_am: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_pm: Mapped[str] = mapped_column(
        Enum(*ATTENDANCE_STATUSES, name="attendance_status"),
        default="absent",
    )
    remarks_pm: Mapped[str | None] = mapped_column(Text, nullable=True)

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
            f"date={self.date!r}, status_am={self.status_am!r}, "
            f"status_pm={self.status_pm!r})>"
        )
