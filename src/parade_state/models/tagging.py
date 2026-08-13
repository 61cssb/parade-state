"""Tagging overlay models.

A Tagging is an overlay of person → subunit remappings applied on top of a
Nominal Roll. Taggings never mutate the underlying NR's personnel or subunit
data; they are consumed downstream (attendance / groupings) to render the
remapped structure. A tagging is linked to exactly one nominal roll; cloning
to another NR creates an independent tagging whose entries point at the
target NR's personnel rows.
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
    """A named overlay of person → subunit remappings on a single nominal roll.

    ``label`` is globally unique (server-enforced) so admins can refer to a
    tagging unambiguously across NRs. Deleting the NR cascades to its taggings.
    """

    __tablename__ = "taggings"

    label: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    nominal_roll_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("nominal_rolls.id", ondelete="CASCADE"),
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
    nominal_roll: Mapped["NominalRoll"] = relationship(back_populates="taggings")
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

    def __repr__(self) -> str:
        return (
            f"<Tagging(label={self.label!r}, "
            f"nominal_roll_id={self.nominal_roll_id!r})>"
        )


class TaggingEntry(Base):
    """A single person → subunit remap within a Tagging.

    Mirrors the 4-string unit hierarchy used by ``Personnel`` and
    ``DeploymentPersonnelOverride``. ``from_*`` is an optional snapshot of the
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
