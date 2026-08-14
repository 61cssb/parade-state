"""Audit log model."""

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from parade_state.utils import utc_dt

from ..db import Base


class AuditLog(Base):
    """Sequential append-only log of all system changes."""

    __tablename__ = "audit_logs"

    timestamp: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()), index=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(
        Enum(
            "attendance",
            "grouping",
            "session",
            "user",
            "csv_upload",
            "nominal_roll",
            "personnel",
            "access_level",
            "column_mapping",
            name="audit_entity_type",
        ),
        index=True,
    )
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(
        Enum(
            "create",
            "update",
            "delete",
            "archive",
            "close",
            "finalize",
            name="audit_action",
        ),
    )
    changes: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditLog(entity_type={self.entity_type!r}, "
            f"entity_id={self.entity_id!r}, action={self.action!r})>"
        )
