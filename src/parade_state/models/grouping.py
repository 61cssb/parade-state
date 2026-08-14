"""Grouping and related models.

A Grouping is the umbrella term covering standard operational groupings,
adhoc groupings (detachments, details), and vehicle manifests. Each
grouping entry has a ``mode`` (``"standard"``, ``"adhoc"``, ``"vehicle"``).
"""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .access import GroupingUserAccess, UserSubunitScope
    from .csv_ingestion import NominalRoll
    from .personnel import Personnel


class Grouping(Base):
    """Operational grouping based on a nominal roll, with overrides and validity window.

    ``mode`` distinguishes standard groupings (with a mandatory validity
    window) from adhoc groupings and vehicle manifests.
    """

    __tablename__ = "groupings"

    name: Mapped[str] = mapped_column(String(255))
    nominal_roll_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nominal_rolls.id", ondelete="RESTRICT")
    )
    mode: Mapped[str] = mapped_column(
        Enum(
            "standard",
            "adhoc",
            "vehicle",
            name="grouping_mode",
        ),
        default="standard",
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "draft",
            "active",
            "inactive",
            "archived",
            "closed",
            "finalized",
            name="grouping_status",
        ),
        default="draft",
    )
    valid_from: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    valid_until: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    scheduled_activation: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    personnel_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    activated_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    deactivated_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    nominal_roll: Mapped["NominalRoll"] = relationship(back_populates="groupings")
    personnel_overrides: Mapped[list["GroupingPersonnelOverride"]] = relationship(
        back_populates="grouping", cascade="all, delete-orphan"
    )
    notes_records: Mapped[list["GroupingNotes"]] = relationship(
        back_populates="grouping", cascade="all, delete-orphan"
    )
    user_accesses: Mapped[list["GroupingUserAccess"]] = relationship(
        back_populates="grouping", cascade="all, delete-orphan"
    )
    user_scopes: Mapped[list["UserSubunitScope"]] = relationship(
        back_populates="grouping", cascade="all, delete-orphan"
    )
    personnel_exclusions: Mapped[list["GroupingPersonnelExclusion"]] = relationship(
        back_populates="grouping", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Grouping(name={self.name!r}, status={self.status!r})>"


class GroupingPersonnelOverride(Base):
    """Per-grouping personnel assignment remap (override nominal roll hierarchy).

    Carries two free-form per-entry fields: ``checkbox`` (boolean marker) and
    ``remarks`` (text), giving commanders flexibility during ops.
    """

    __tablename__ = "grouping_personnel_overrides"

    grouping_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groupings.id", ondelete="CASCADE")
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE")
    )
    unit: Mapped[str] = mapped_column(String(255))
    sub_unit_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkbox: Mapped[bool] = mapped_column(Boolean, default=False)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))

    # Relationships
    grouping: Mapped[Grouping] = relationship(back_populates="personnel_overrides")
    personnel: Mapped["Personnel"] = relationship(back_populates="grouping_overrides")

    __table_args__ = (
        UniqueConstraint(
            "grouping_id",
            "personnel_id",
            name="unique_grouping_personnel_override",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GroupingPersonnelOverride(grouping_id={self.grouping_id!r}, "
            f"personnel_id={self.personnel_id!r})>"
        )


class GroupingPersonnelExclusion(Base):
    """Records a personnel excluded from a specific grouping's roster."""

    __tablename__ = "grouping_personnel_exclusions"

    grouping_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groupings.id", ondelete="CASCADE")
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE")
    )
    excluded_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    excluded_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )

    # Relationships
    grouping: Mapped[Grouping] = relationship(back_populates="personnel_exclusions")

    __table_args__ = (
        UniqueConstraint(
            "grouping_id",
            "personnel_id",
            name="unique_grouping_personnel_exclusion",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GroupingPersonnelExclusion(grouping_id={self.grouping_id!r}, "
            f"personnel_id={self.personnel_id!r})>"
        )


class GroupingNotes(Base):
    """Canonical store for personnel notes, scoped to grouping. Shared across all sessions."""

    __tablename__ = "grouping_notes"

    grouping_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groupings.id", ondelete="CASCADE")
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE")
    )
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    updated_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    notes_version: Mapped[int] = mapped_column(Integer, default=1)

    # Relationships
    grouping: Mapped[Grouping] = relationship(back_populates="notes_records")
    personnel: Mapped["Personnel"] = relationship(back_populates="grouping_notes")

    __table_args__ = (
        UniqueConstraint(
            "grouping_id",
            "personnel_id",
            name="unique_grouping_personnel_notes",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GroupingNotes(grouping_id={self.grouping_id!r}, "
            f"personnel_id={self.personnel_id!r})>"
        )
