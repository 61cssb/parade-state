"""Attendance models.

Attendance is taken twice daily (AM/PM, hardcoded) against a scope that is
either a Nominal Roll or a Tagging overlay on it. One ``Attendance`` row per
``(personnel, date)`` carries status + remarks for both AM and PM slots.

Before attendance can be taken for an NR, a super-admin must activate a scope
(an ``AttendanceScope`` row). The active scope — NR itself or a specific
Tagging — is what attendance is carried out against, and is displayed at the
top of the attendance view.
"""

from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .csv_ingestion import NominalRoll
    from .personnel import Personnel
    from .tagging import Tagging

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

    ``nominal_roll_id`` is always the parent NR of the personnel row.
    ``tagging_id`` snapshots the active Tagging overlay at creation time
    (``None`` means the NR itself was the active scope). The unique constraint
    enforces one record per person per day.
    """

    __tablename__ = "attendance"

    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE"), index=True
    )
    nominal_roll_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nominal_rolls.id", ondelete="CASCADE"), index=True
    )
    tagging_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("taggings.id", ondelete="RESTRICT"), nullable=True, index=True
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
    tagging: Mapped["Tagging | None"] = relationship()

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


class AttendanceScope(Base):
    """The active attendance scope for a Nominal Roll (1:1 with NR).

    A super-admin activates a scope before attendance can be taken. When
    ``tagging_id`` is ``None`` the NR itself is the scope; otherwise the named
    Tagging overlay is. At most one row per NR (enforced by a unique
    constraint on ``nominal_roll_id``).
    """

    __tablename__ = "attendance_scope"

    nominal_roll_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nominal_rolls.id", ondelete="CASCADE"),
        unique=True, index=True,
    )
    tagging_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("taggings.id", ondelete="RESTRICT"), nullable=True
    )
    activated_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    activated_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))

    # Relationships
    nominal_roll: Mapped["NominalRoll"] = relationship(
        back_populates="attendance_scope"
    )
    tagging: Mapped["Tagging | None"] = relationship()
    activator = relationship("User", foreign_keys=[activated_by])

    def __repr__(self) -> str:
        return (
            f"<AttendanceScope(nominal_roll_id={self.nominal_roll_id!r}, "
            f"tagging_id={self.tagging_id!r})>"
        )
