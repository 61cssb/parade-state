"""CSV ingestion and establishment models."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base

if TYPE_CHECKING:
    from .personnel import Personnel


class Estab(Base):
    """Base personnel roster, sourced from CSV, pinned by CAA date."""

    __tablename__ = "estabs"

    caa: Mapped[datetime] = mapped_column(Date, unique=True, index=True)
    csv_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        Enum("draft", "confirmed", "archived", name="estab_status"),
        default="draft",
    )
    personnel_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    uploaded_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    csv_uploads: Mapped[list["CsvUpload"]] = relationship(
        back_populates="estab", cascade="all, delete-orphan"
    )
    column_metadata: Mapped[list["ColumnMetadata"]] = relationship(
        back_populates="estab", cascade="all, delete-orphan"
    )
    personnel: Mapped[list["Personnel"]] = relationship(
        back_populates="estab", cascade="all, delete-orphan"
    )
    deployments: Mapped[list["Deployment"]] = relationship(
        back_populates="estab", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Estab(caa={self.caa!r}, status={self.status!r})>"


class CsvUpload(Base):
    """Raw CSV file storage (immutable, append-only)."""

    __tablename__ = "csv_uploads"

    estab_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("estabs.id"), nullable=True
    )
    raw_content: Mapped[bytes] = mapped_column(LargeBinary)
    sha256_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    line_count: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    uploaded_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id")
    )
    mapping_confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    diff_confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    status: Mapped[str] = mapped_column(
        Enum(
            "received",
            "mapping_confirmed",
            "diff_confirmed",
            "failed",
            name="csv_upload_status",
        ),
        default="received",
    )

    # Relationships
    estab: Mapped[Estab | None] = relationship(back_populates="csv_uploads")

    def __repr__(self) -> str:
        return f"<CsvUpload(hash={self.sha256_hash[:8]}..., status={self.status!r})>"


class ColumnMapping(Base):
    """Global mapping table: raw CSV column names → canonical app column names."""

    __tablename__ = "column_mappings"

    raw_name: Mapped[str] = mapped_column(String(255), index=True)
    canonical_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(
        Enum(
            "auto_detected",
            "admin_confirmed",
            "deprecated",
            name="column_mapping_status",
        ),
        default="auto_detected",
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    deprecated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ColumnMapping(raw={self.raw_name!r}, "
            f"canonical={self.canonical_name!r}, status={self.status!r})>"
        )


class ColumnMetadata(Base):
    """Per-CSV column metadata: original headers, canonical mapping, inferred types, sensitivity."""

    __tablename__ = "column_metadata"

    estab_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("estabs.id", ondelete="CASCADE")
    )
    csv_upload_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("csv_uploads.id", ondelete="CASCADE")
    )
    original_name: Mapped[str] = mapped_column(String(255))
    canonical_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    inferred_type: Mapped[str] = mapped_column(
        Enum(
            "string",
            "integer",
            "date",
            "boolean",
            "json",
            name="column_data_type",
        ),
        default="string",
    )
    sensitivity_level_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("access_levels.id"), nullable=True
    )
    is_required: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    estab: Mapped[Estab] = relationship(back_populates="column_metadata")
    sensitivity_level: Mapped["AccessLevel"] = relationship(back_populates="column_metadata")

    __table_args__ = (
        UniqueConstraint("estab_id", "original_name", name="unique_estab_column"),
    )

    def __repr__(self) -> str:
        return (
            f"<ColumnMetadata(original={self.original_name!r}, "
            f"canonical={self.canonical_name!r})>"
        )