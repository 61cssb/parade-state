"""feature access matrix

Revision ID: y6f7a8b9c0d1
Revises: x5e6f7a8b9c0
Create Date: 2026-08-27 00:00:00.000000

Adds the ``feature_access`` table (issue 37): one row per (feature, role)
a super-admin has explicitly configured in Settings. Absent row = enabled
(fail-open); ``super_admin`` is never configurable. Seeded/serviced via
the Settings UI — no data migration.

Also widens ``audit_entity_type`` with ``feature_access`` so the Settings
save endpoint can audit-log matrix changes. On Postgres the native enum
is widened in place with ALTER TYPE ... ADD VALUE (precedent
x5e6f7a8b9c0); on SQLite enum names are metadata-only, nothing to do.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "y6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "x5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ENTITY_TYPES = ("feature_access",)


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
    """Create feature_access and widen audit_entity_type."""
    op.create_table(
        "feature_access",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("feature_key", sa.String(length=50), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feature_key", "role", name="uq_feature_access_key_role"),
    )
    op.create_index("ix_feature_access_id", "feature_access", ["id"], unique=False)

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    existing = _existing_enum_values(bind, "audit_entity_type")
    for value in NEW_ENTITY_TYPES:
        if value not in existing:
            op.execute(f"ALTER TYPE audit_entity_type ADD VALUE '{value}'")


def downgrade() -> None:
    """Drop feature_access; keep the widened audit_entity_type.

    Postgres cannot drop values from an enum type without a rebuild, and
    leftover unused values are harmless (same stance as x5e6f7a8b9c0).
    """
    op.drop_index("ix_feature_access_id", table_name="feature_access")
    op.drop_table("feature_access")
