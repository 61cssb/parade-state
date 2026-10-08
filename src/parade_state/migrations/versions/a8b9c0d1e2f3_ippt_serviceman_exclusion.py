"""IPPT serviceman exclusion (remove-from-tracking)

Revision ID: a8b9c0d1e2f3
Revises: z7g8h9i0j1k2
Create Date: 2026-10-08 00:00:00.000000

Adds the remove-from-tracking columns to ``ippt_servicemen`` (decided
2026-10-08, docs/NEXT_PHASE.md §9a): ``excluded`` hides a serviceman
from the dashboard/tier views while the row (and its history) is
retained, and ``exclusion_reason`` records why. Also widens
``audit_entity_type`` with ``ippt_serviceman`` so exclusion and
re-inclusion are audit-logged (precedent z7g8h9i0j1k2); on SQLite enum
names are metadata-only, nothing to do.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "z7g8h9i0j1k2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ENTITY_TYPES = ("ippt_serviceman",)


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
    """Add the exclusion columns; widen audit_entity_type."""
    op.add_column(
        "ippt_servicemen",
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "ippt_servicemen",
        sa.Column("exclusion_reason", sa.Text(), nullable=True),
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    existing = _existing_enum_values(bind, "audit_entity_type")
    for value in NEW_ENTITY_TYPES:
        if value not in existing:
            op.execute(f"ALTER TYPE audit_entity_type ADD VALUE '{value}'")


def downgrade() -> None:
    """Drop the exclusion columns; keep the widened audit_entity_type.

    Postgres cannot drop values from an enum type without a rebuild, and
    leftover unused values are harmless (same stance as z7g8h9i0j1k2).
    """
    op.drop_column("ippt_servicemen", "exclusion_reason")
    op.drop_column("ippt_servicemen", "excluded")
