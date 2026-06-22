"""Authentication session models for user session management."""

from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt
from parade_state.utils.utc_dt import datetime

from ..db import Base

if TYPE_CHECKING:
    from .access import User


class UserSession(Base):
    """User authentication session for managing login state and access control."""

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(
        back_populates="sessions", cascade="all, delete"
    )

    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )

    def is_valid(self) -> bool:
        """Check if session is still valid (not expired)."""
        return not utc_dt.is_expired(self.expires_at)

    def refresh_last_accessed(self) -> None:
        """Update the last accessed timestamp."""
        self.last_accessed_at = utc_dt.ensure_naive(utc_dt.utcnow())

    def __repr__(self) -> str:
        return (
            f"<UserSession(token={self.token[:10]}..., user_id={self.user_id!r}, "
            f"role={self.role!r}, expires_at={self.expires_at!r})>"
        )
