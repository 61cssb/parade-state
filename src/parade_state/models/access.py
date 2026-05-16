"""Access control and authentication models."""

from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import utc_dt

if TYPE_CHECKING:
    from .auth_session import UserSession
    from .csv_ingestion import ColumnMetadata
    from .deployment import Deployment


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
            "pending",
            "active",
            "suspended",
            "unrecognised",
            name="user_status",
        ),
        default="pending",
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
    subunit_scopes: Mapped[list["UserSubunitScope"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserSubunitScope.user_id",
    )
    deployment_accesses: Mapped[list["DeploymentUserAccess"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="DeploymentUserAccess.user_id",
    )

    def __repr__(self) -> str:
        return f"<User(email={self.email!r}, status={self.status!r})>"


class UserSubunitScope(Base):
    """Links a user to specific subunit(s) within each deployment."""

    __tablename__ = "user_subunit_scopes"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    deployment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_unit_3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )

    # Relationships
    user: Mapped[User] = relationship(
        back_populates="subunit_scopes", foreign_keys=[user_id]
    )
    deployment: Mapped["Deployment"] = relationship(back_populates="user_scopes")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "deployment_id",
            "unit",
            "sub_unit_1",
            "sub_unit_2",
            "sub_unit_3",
            name="unique_user_deployment_scope",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<UserSubunitScope(user_id={self.user_id!r}, "
            f"deployment_id={self.deployment_id!r}, "
            f"unit={self.unit!r})>"
        )


class DeploymentUserAccess(Base):
    """Grants a user access to a specific deployment."""

    __tablename__ = "deployment_user_accesses"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    deployment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    granted_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    granted_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    revoked_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)

    # Relationships
    user: Mapped[User] = relationship(
        back_populates="deployment_accesses", foreign_keys=[user_id]
    )
    deployment: Mapped["Deployment"] = relationship(back_populates="user_accesses")

    __table_args__ = (
        UniqueConstraint(
            "user_id", "deployment_id", name="unique_user_deployment_access"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DeploymentUserAccess(user_id={self.user_id!r}, "
            f"deployment_id={self.deployment_id!r})>"
        )
