"""Defaulter report tables

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-11-08 00:00:00.000000

Adds the three ``defaulter_*`` tables of the quarterly defaulter
release (docs/IPPT_MONITORING.md §7): the per-quarter batch, its rows,
and the quarantined-row reject list. The feature rides FEATURE_IPPT
(local + Railway dev), but the schema migrates cleanly on both
dialects like any other table. Also widens ``audit_entity_type`` with
``defaulter_report`` (ingests) and ``defaulter_row`` (reason/remarks
edits) so the endpoints can audit-log (precedent z7g8h9i0j1k2); on
SQLite enum names are metadata-only, nothing to do.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ENTITY_TYPES = ("defaulter_report", "defaulter_row")

# BigInteger PKs: bigserial on Postgres, plain integer rowid on SQLite.
BigId = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def _existing_enum_values(bind, type_name: str) -> set[str]:
    """Labels currently present in a native Postgres enum type."""
    return {
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :type_name"
            ),
            {"type_name": type_name},
        ).all()
    }


def upgrade() -> None:
    """Create the defaulter_* tables and widen audit_entity_type."""
    op.create_table(
        "defaulter_reports",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("quarter", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "quarter IN (1, 2, 3, 4)", name="ck_defaulter_reports_quarter"
        ),
        sa.CheckConstraint(
            "status IN ('current', 'archived')", name="ck_defaulter_reports_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("year", "quarter", name="uq_defaulter_report_year_quarter"),
    )
    op.create_index("ix_defaulter_reports_status", "defaulter_reports", ["status"])

    op.create_table(
        "defaulter_rows",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("report_id", BigId, nullable=False),
        sa.Column("row_type", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=255), nullable=False),
        sa.Column("pers_no", sa.String(length=20), nullable=True),
        sa.Column("rank", sa.String(length=16), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("full_name_norm", sa.String(length=255), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=True),
        sa.Column("match_status", sa.String(length=16), nullable=True),
        sa.Column("sub_unit", sa.String(length=255), nullable=True),
        sa.Column("offence_type", sa.String(length=64), nullable=False),
        sa.Column("offence_window", sa.String(length=64), nullable=False),
        sa.Column("default_count", sa.Integer(), nullable=False),
        sa.Column("defaulted_count", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "row_type IN ('pending', 'archived')", name="ck_defaulter_row_type"
        ),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ('hsp', 'attitude_issue',"
            " 'uncontactable', 'others')",
            name="ck_defaulter_row_reason",
        ),
        sa.CheckConstraint(
            "match_status IN ('matched', 'unmatched', 'ambiguous')",
            name="ck_defaulter_row_match_status",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"], ["defaulter_reports.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_defaulter_rows_report_id", "defaulter_rows", ["report_id"])
    op.create_index(
        "ix_defaulter_rows_full_name_norm", "defaulter_rows", ["full_name_norm"]
    )
    op.create_index(
        "ix_defaulter_rows_personnel_id", "defaulter_rows", ["personnel_id"]
    )

    op.create_table(
        "defaulter_quarantined_rows",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("report_id", BigId, nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("raw_row", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"], ["defaulter_reports.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_defaulter_quarantined_rows_report_id",
        "defaulter_quarantined_rows",
        ["report_id"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    existing = _existing_enum_values(bind, "audit_entity_type")
    for value in NEW_ENTITY_TYPES:
        if value not in existing:
            op.execute(f"ALTER TYPE audit_entity_type ADD VALUE '{value}'")


def downgrade() -> None:
    """Drop the defaulter_* tables; keep the widened audit_entity_type.

    Postgres cannot drop values from an enum type without a rebuild, and
    leftover unused values are harmless (same stance as z7g8h9i0j1k2).
    """
    op.drop_index(
        "ix_defaulter_quarantined_rows_report_id",
        table_name="defaulter_quarantined_rows",
    )
    op.drop_table("defaulter_quarantined_rows")
    op.drop_index("ix_defaulter_rows_personnel_id", table_name="defaulter_rows")
    op.drop_index("ix_defaulter_rows_full_name_norm", table_name="defaulter_rows")
    op.drop_index("ix_defaulter_rows_report_id", table_name="defaulter_rows")
    op.drop_table("defaulter_rows")
    op.drop_index("ix_defaulter_reports_status", table_name="defaulter_reports")
    op.drop_table("defaulter_reports")
