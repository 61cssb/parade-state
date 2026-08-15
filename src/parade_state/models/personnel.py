"""Personnel and nominal roll models."""

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .attendance import Attendance
    from .csv_ingestion import NominalRoll
    from .deferments import Deferment
    from .grouping import GroupingNotes, GroupingPersonnelOverride


class Personnel(Base):
    """Individual personnel record, sourced from a CSV nominal roll.

    Identity: ``id`` is the row PK (one row per nominal-roll-person pairing) and
    the FK target for dependent tables. ``pers_no`` is the canonical cross-roll
    person identifier — the external personnel number from the CSV ``Pers``
    column. One pers_no is one person globally: every row belonging to the same
    individual across rolls shares it, and no two distinct persons ever share
    one (enforced by the external system that mints the numbers). NULL when the
    CSV row omitted it; never an empty string.
    """

    __tablename__ = "personnel"

    nominal_roll_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nominal_rolls.id", ondelete="CASCADE"), index=True
    )
    pers_no: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    rank: Mapped[str] = mapped_column(String(50), index=True)
    category: Mapped[str] = mapped_column(
        Enum("Officer", "WOSE", name="personnel_category"),
        index=True,
    )
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
    nominal_roll: Mapped["NominalRoll"] = relationship(back_populates="personnel")
    grouping_overrides: Mapped[list["GroupingPersonnelOverride"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )
    grouping_notes: Mapped[list["GroupingNotes"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )
    attendance: Mapped[list["Attendance"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )
    deferments: Mapped[list["Deferment"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("nominal_roll_id", "pers_no", name="uq_personnel_nominal_roll_pers_no"),
        {"schema": None},  # Default schema
    )

    def __repr__(self) -> str:
        return (
            f"<Personnel(pers_no={self.pers_no!r}, "
            f"name={self.full_name!r}, status={self.status!r})>"
        )
