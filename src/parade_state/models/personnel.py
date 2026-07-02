"""Personnel and establishment models."""

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import ids, utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .attendance import AttendanceRecord
    from .csv_ingestion import Estab
    from .deferments import Deferment
    from .deployment import DeploymentNotes, DeploymentPersonnelOverride


class Personnel(Base):
    """Individual personnel record, sourced from CSV estab.

    Identity: ``id`` is the row PK (one row per estab-person pairing). ``short_id``
    is the cross-estab person identifier — an 8-char base62 string shared by every
    row belonging to the same individual across estabs. Minted server-side; never
    sourced from the CSV (``pers_no`` is dropped on parse, never stored).
    """

    __tablename__ = "personnel"

    estab_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("estabs.id", ondelete="CASCADE"), index=True
    )
    short_id: Mapped[str] = mapped_column(String(8), default=ids.short_id, index=True)
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
    callup_status: Mapped[str] = mapped_column(
        Enum("Called Up", "Not Called Up", "Deferred", name="personnel_callup_status"),
        default="Called Up",
        index=True,
    )
    created_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True, index=True)
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
    deferments: Mapped[list["Deferment"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("estab_id", "short_id", name="uq_personnel_estab_short_id"),
        {"schema": None},  # Default schema
    )

    def __repr__(self) -> str:
        return (
            f"<Personnel(short_id={self.short_id!r}, "
            f"name={self.full_name!r}, status={self.status!r})>"
        )
