"""Attendance and session models."""

from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .deployment import Deployment
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


class Session(Base):
    """AM or PM attendance window, explicitly created by admin, linked to deployment."""

    __tablename__ = "sessions"

    deployment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="CASCADE")
    )
    date: Mapped[utc_dt.date] = mapped_column(Date, index=True)
    session_type: Mapped[str] = mapped_column(
        Enum("AM", "PM", name="session_type"),
    )
    status: Mapped[str] = mapped_column(
        Enum("open", "closed", "finalized", name="session_status"),
        default="open",
    )
    created_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    opened_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    closed_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    closed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )

    # Relationships
    deployment: Mapped["Deployment"] = relationship(back_populates="sessions")
    attendance_records: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "deployment_id",
            "date",
            "session_type",
            name="unique_deployment_date_session_type",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Session(deployment_id={self.deployment_id!r}, "
            f"date={self.date!r}, type={self.session_type!r}, status={self.status!r})>"
        )


class AttendanceRecord(Base):
    """Per-personnel per-session attendance status, remarks, and snapshots."""

    __tablename__ = "attendance_records"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE")
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE")
    )
    deployment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(
        Enum(
            *ATTENDANCE_STATUSES,
            name="attendance_status",
        ),
        default="absent",
    )
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_1_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_2_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_3_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    updated_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    last_edit_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    last_edit_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    is_retroactive_edit: Mapped[bool] = mapped_column(default=False)

    # Relationships
    session: Mapped[Session] = relationship(back_populates="attendance_records")
    personnel: Mapped["Personnel"] = relationship(back_populates="attendance_records")
    deployment: Mapped["Deployment"] = relationship(back_populates="attendance_records")

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "personnel_id",
            name="unique_session_personnel_attendance",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AttendanceRecord(session_id={self.session_id!r}, "
            f"personnel_id={self.personnel_id!r}, status={self.status!r})>"
        )
