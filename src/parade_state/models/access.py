"""Access control and authentication models."""

from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import utc_dt

if TYPE_CHECKING:
    from .auth_session import UserSession
    from .csv_ingestion import ColumnMetadata, NominalRoll


class AccessLevel(Base):
    """Ordered vocabulary of access scopes (e.g., unit, coy, platoon, section)."""

    __tablename__ = "access_levels"

    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    level_order: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )

    # Relationships
    column_metadata: Mapped[list["ColumnMetadata"]] = relationship(
        back_populates="sensitivity_level"
    )

    def __repr__(self) -> str:
        return f"<AccessLevel(name={self.name!r}, level_order={self.level_order})>"


class User(Base):
    """Google-authenticated users with role and access scope."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",  # legacy value, unused (kept for the DB enum)
            "active",
            "suspended",
            "unrecognised",
            name="user_status",
        ),
        default="unrecognised",
    )
    role: Mapped[str] = mapped_column(
        Enum("super_admin", "admin", "user", name="user_role"),
        default="user",
    )
    access_level_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("access_levels.id"), nullable=True
    )
    first_sign_in_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    last_sign_in_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    updated_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )

    # Relationships
    access_level: Mapped[AccessLevel | None] = relationship(
        foreign_keys=[access_level_id]
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    subunit_assignments: Mapped[list["UserSubunitAssignment"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserSubunitAssignment.user_id",
    )

    def __repr__(self) -> str:
        return f"<User(email={self.email!r}, status={self.status!r})>"


class UserSubunitAssignment(Base):
    """Grants a user attendance-update rights for one sub_unit_1 on an NR.

    NR-scoped (issue #4): attendance access is no longer grouping-scoped.
    A user may only upsert attendance for personnel whose effective
    ``sub_unit_1`` (canonical, or remapped under the active Tagging scope)
    matches one of their assignments on that NR. ``super_admin`` bypasses
    entirely. Deny-by-default: a user with no assignments on an NR has no
    attendance-write access there.
    """

    __tablename__ = "user_subunit_assignments"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    nominal_roll_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("nominal_rolls.id", ondelete="CASCADE"), index=True
    )
    sub_unit_1: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )

    # Relationships
    user: Mapped[User] = relationship(
        back_populates="subunit_assignments", foreign_keys=[user_id]
    )
    nominal_roll: Mapped["NominalRoll"] = relationship(
        back_populates="subunit_assignments"
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "nominal_roll_id",
            "sub_unit_1",
            name="uq_user_subunit_assignment",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<UserSubunitAssignment(user_id={self.user_id!r}, "
            f"nominal_roll_id={self.nominal_roll_id!r}, "
            f"sub_unit_1={self.sub_unit_1!r})>"
        )
