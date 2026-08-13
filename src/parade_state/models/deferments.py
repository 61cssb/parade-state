"""Deferment model - tracks personnel deferments linked to nominal roll personnel records."""

from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .access import User
    from .personnel import Personnel


class Deferment(Base):
    """A deferment request for a single personnel record.

    ``rank_name`` and ``sub_unit`` are snapshotted from the linked personnel at
    creation time so the deferment remains an accurate record even if the
    personnel row is later edited or the nominal roll is superseded.
    """

    __tablename__ = "deferments"

    personnel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("personnel.id", ondelete="CASCADE"), index=True
    )
    rank_name: Mapped[str] = mapped_column(String(255))
    sub_unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str] = mapped_column(
        Enum(
            "Honeymoon",
            "Work",
            "Full-time studies",
            "Other",
            "Medical Grounds",
            "Examination",
            "New employment",
            "Special employment",
            "Compassionate",
            "Childbirth",
            "Part-time studies",
            "Newly Established Business (Local)",
            name="deferment_reason",
        ),
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "Approved",
            "Withdrawn",
            "Rejected",
            "To Resubmit",
            "Time off arrangement",
            "Pending action",
            "Not called up",
            "Do not call up",
            name="deferment_status",
        ),
        default="Pending action",
        index=True,
    )
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    oc_updates: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True, index=True)
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )

    # Relationships
    personnel: Mapped["Personnel"] = relationship(back_populates="deferments")
    creator: Mapped["User"] = relationship(
        foreign_keys=[created_by],
        primaryjoin="Deferment.created_by == User.id",
    )
    updater: Mapped["User | None"] = relationship(
        foreign_keys=[updated_by],
        primaryjoin="Deferment.updated_by == User.id",
    )

    def __repr__(self) -> str:
        return (
            f"<Deferment(rank_name={self.rank_name!r}, "
            f"reason={self.reason!r}, status={self.status!r})>"
        )
