"""Deployment and related models."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base

if TYPE_CHECKING:
    from .access import DeploymentUserAccess, UserSubunitScope
    from .attendance import AttendanceRecord, Session
    from .csv_ingestion import Estab
    from .personnel import Personnel


class Deployment(Base):
    """Operational deployment based on an estab, with overrides and validity window."""

    __tablename__ = "deployments"

    name: Mapped[str] = mapped_column(String(255))
    estab_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("estabs.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "draft",
            "active",
            "inactive",
            "archived",
            "closed",
            "finalized",
            name="deployment_status",
        ),
        default="draft",
    )
    valid_from: Mapped[datetime] = mapped_column()
    valid_until: Mapped[datetime] = mapped_column()
    scheduled_activation: Mapped[datetime | None] = mapped_column(nullable=True)
    personnel_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    activated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    estab: Mapped["Estab"] = relationship(back_populates="deployments")
    personnel_overrides: Mapped[list["DeploymentPersonnelOverride"]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )
    notes_records: Mapped[list["DeploymentNotes"]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )
    user_accesses: Mapped[list["DeploymentUserAccess"]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )
    user_scopes: Mapped[list["UserSubunitScope"]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )
    attendance_records: Mapped[list["AttendanceRecord"]] = relationship(
        back_populates="deployment"
    )

    def __repr__(self) -> str:
        return f"<Deployment(name={self.name!r}, status={self.status!r})>"


class DeploymentPersonnelOverride(Base):
    """Per-deployment personnel assignment remap (override estab hierarchy)."""

    __tablename__ = "deployment_personnel_overrides"

    deployment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="CASCADE")
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE")
    )
    unit: Mapped[str] = mapped_column(String(255))
    sub_unit_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    deployment: Mapped[Deployment] = relationship(back_populates="personnel_overrides")
    personnel: Mapped["Personnel"] = relationship(back_populates="deployment_overrides")

    __table_args__ = (
        UniqueConstraint(
            "deployment_id",
            "personnel_id",
            name="unique_deployment_personnel_override",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DeploymentPersonnelOverride(deployment_id={self.deployment_id!r}, "
            f"personnel_id={self.personnel_id!r})>"
        )


class DeploymentNotes(Base):
    """Canonical store for personnel notes, scoped to deployment. Shared across all sessions."""

    __tablename__ = "deployment_notes"

    deployment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="CASCADE")
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE")
    )
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    notes_version: Mapped[int] = mapped_column(Integer, default=1)

    # Relationships
    deployment: Mapped[Deployment] = relationship(back_populates="notes_records")
    personnel: Mapped["Personnel"] = relationship(back_populates="deployment_notes")

    __table_args__ = (
        UniqueConstraint(
            "deployment_id",
            "personnel_id",
            name="unique_deployment_personnel_notes",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DeploymentNotes(deployment_id={self.deployment_id!r}, "
            f"personnel_id={self.personnel_id!r})>"
        )
