"""Tagging overlay models.

A Tagging is the single overlay of person → subunit remappings applied on
top of a Nominal Roll — 1:1 with the NR. Taggings never mutate the
underlying NR's personnel or subunit data; they are consumed downstream
(attendance / groupings / the NR browser) to render the effective structure.
Edits to a CSV-sourced NR's unit/subunit assignments land here as
TaggingEntry rows; the NR itself stays read-only.
"""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .access import User
    from .csv_ingestion import NominalRoll
    from .personnel import Personnel


class Tagging(Base):
    """The overlay of person → subunit remappings on a Nominal Roll (1:1).

    One Tagging per NR — enforced by a unique constraint on
    ``nominal_roll_id``. Auto-created on NR ingestion. ``label`` is optional
    and informational only; the NR identity is the natural key. Deleting the
    NR cascades to its tagging.
    """

    __tablename__ = "taggings"

    label: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    nominal_roll_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("nominal_rolls.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True, index=True)
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )

    # Relationships
    nominal_roll: Mapped["NominalRoll"] = relationship(back_populates="tagging")
    entries: Mapped[list["TaggingEntry"]] = relationship(
        back_populates="tagging", cascade="all, delete-orphan"
    )
    creator: Mapped["User"] = relationship(
        foreign_keys=[created_by],
        primaryjoin="Tagging.created_by == User.id",
    )
    updater: Mapped["User | None"] = relationship(
        foreign_keys=[updated_by],
        primaryjoin="Tagging.updated_by == User.id",
    )

    __table_args__ = (
        UniqueConstraint("nominal_roll_id", name="uq_taggings_nominal_roll_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Tagging(label={self.label!r}, "
            f"nominal_roll_id={self.nominal_roll_id!r})>"
        )


class TaggingEntry(Base):
    """A single person → subunit remap within a Tagging.

    Mirrors the 4-string unit hierarchy used by ``Personnel`` and
    ``GroupingPersonnelOverride``. ``from_*`` is an optional snapshot of the
    person's canonical subunit on the parent NR (inferred at creation time if
    omitted); ``to_*`` is the remap target. ``to_unit`` is always required.

    One remap per person per tagging — enforced by
    ``UniqueConstraint(tagging_id, personnel_id)``.
    """

    __tablename__ = "tagging_entries"

    tagging_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("taggings.id", ondelete="CASCADE"), index=True
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE"), index=True
    )
    from_unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_sub_unit_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_sub_unit_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_sub_unit_3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_unit: Mapped[str] = mapped_column(String(255))
    to_sub_unit_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_sub_unit_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_sub_unit_3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )

    # Relationships
    tagging: Mapped[Tagging] = relationship(back_populates="entries")
    personnel: Mapped["Personnel"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "tagging_id", "personnel_id", name="uq_tagging_entry_person"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<TaggingEntry(tagging_id={self.tagging_id!r}, "
            f"personnel_id={self.personnel_id!r}, to_unit={self.to_unit!r})>"
        )
