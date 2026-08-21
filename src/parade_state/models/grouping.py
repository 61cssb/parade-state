"""Grouping models (issue 26 redesign).

A Grouping is a labelled, closed vocabulary of groups (``GroupingGroup``)
based on one nominal roll. Servicemen on that roll hold memberships
(``GroupingMembership``) in the groups, plus a per-grouping checkbox and
free-text remarks (``GroupingMemberState``) whose semantics are left to
each unit's standardisation.

Groupings never read or write attendance — there is deliberately no
validity window, status lifecycle, or attendance coupling anywhere in
this module.
"""

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .csv_ingestion import NominalRoll
    from .personnel import Personnel


class Grouping(Base):
    """A labelled set of groups based on a nominal roll.

    ``multiple_membership`` (a serviceman may hold several groups) and
    ``allow_ungrouped`` (a serviceman may hold none) are immutable after
    creation — recreate or clone the grouping to change them.
    """

    __tablename__ = "groupings"

    label: Mapped[str] = mapped_column(String(100), index=True)
    nominal_roll_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nominal_rolls.id", ondelete="RESTRICT"), index=True
    )
    multiple_membership: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_ungrouped: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))

    __table_args__ = (
        UniqueConstraint(
            "nominal_roll_id", "label", name="uq_groupings_nr_label"
        ),
    )

    # Relationships
    nominal_roll: Mapped["NominalRoll"] = relationship(back_populates="groupings")
    groups: Mapped[list["GroupingGroup"]] = relationship(
        back_populates="grouping",
        cascade="all, delete-orphan",
        order_by="GroupingGroup.position",
    )
    memberships: Mapped[list["GroupingMembership"]] = relationship(
        back_populates="grouping", cascade="all, delete-orphan"
    )
    member_state: Mapped[list["GroupingMemberState"]] = relationship(
        back_populates="grouping", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Grouping(label={self.label!r})>"


class GroupingGroup(Base):
    """One group enum within a grouping.

    ``position`` is the manual display order (edit-dialog up/down
    controls); memberships reference the row, so renaming a label
    propagates to every member and deleting the row cascades their
    memberships away.
    """

    __tablename__ = "grouping_groups"

    grouping_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groupings.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    grouping: Mapped[Grouping] = relationship(back_populates="groups")
    memberships: Mapped[list["GroupingMembership"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("grouping_id", "label", name="uq_grouping_group_label"),
    )

    def __repr__(self) -> str:
        return (
            f"<GroupingGroup(grouping_id={self.grouping_id!r}, "
            f"label={self.label!r}, position={self.position})>"
        )


class GroupingMembership(Base):
    """A serviceman's membership of one group within a grouping.

    The single-membership and no-ungrouped rules are application-enforced
    (they cannot be expressed as plain constraints): at most one row per
    serviceman when the grouping has ``multiple_membership=false``, and at
    least one when ``allow_ungrouped=false``.
    """

    __tablename__ = "grouping_memberships"

    grouping_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groupings.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("grouping_groups.id", ondelete="CASCADE"), index=True
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE"), index=True
    )

    # Relationships
    grouping: Mapped[Grouping] = relationship(back_populates="memberships")
    group: Mapped[GroupingGroup] = relationship(back_populates="memberships")
    personnel: Mapped["Personnel"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "grouping_id",
            "personnel_id",
            "group_id",
            name="uq_grouping_membership",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GroupingMembership(grouping_id={self.grouping_id!r}, "
            f"group_id={self.group_id!r}, personnel_id={self.personnel_id!r})>"
        )


class GroupingMemberState(Base):
    """Per-serviceman checkbox and remarks within a grouping.

    One row per (grouping, personnel), independent of how many groups the
    serviceman holds. The fields' meaning is intentionally unspecified —
    standardisation is left to each unit.
    """

    __tablename__ = "grouping_member_state"

    grouping_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groupings.id", ondelete="CASCADE"), index=True
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE"), index=True
    )
    checkbox: Mapped[bool] = mapped_column(Boolean, default=False)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))
    updated_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))

    # Relationships
    grouping: Mapped[Grouping] = relationship(back_populates="member_state")

    __table_args__ = (
        UniqueConstraint(
            "grouping_id",
            "personnel_id",
            name="uq_grouping_member_state",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GroupingMemberState(grouping_id={self.grouping_id!r}, "
            f"personnel_id={self.personnel_id!r})>"
        )
