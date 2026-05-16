"""Personnel and establishment models."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base

if TYPE_CHECKING:
    from .csv_ingestion import Estab


class Personnel(Base):
    """Individual personnel record, sourced from CSV estab."""

    __tablename__ = "personnel"

    estab_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("estabs.id", ondelete="CASCADE"), index=True
    )
    pers_no: Mapped[str] = mapped_column(String(255), index=True)
    rank: Mapped[str] = mapped_column(String(50), index=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    unit: Mapped[str] = mapped_column(String(255), index=True)
    sub_unit_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extra_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", name="personnel_status"),
        default="active",
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )

    # Relationships
    estab: Mapped["Estab"] = relationship(back_populates="personnel")
    deployment_overrides: Mapped[list["DeploymentPersonnelOverride"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )
    deployment_notes: Mapped[list["DeploymentNotes"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )
    attendance_records: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )

    __table_args__ = (
        {"schema": None},  # Default schema
    )

    def __repr__(self) -> str:
        return (
            f"<Personnel(pers_no={self.pers_no!r}, "
            f"name={self.full_name!r}, status={self.status!r})>"
        )
