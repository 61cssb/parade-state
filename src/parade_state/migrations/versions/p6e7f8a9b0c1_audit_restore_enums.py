"""audit restore enums

Revision ID: p6e7f8a9b0c1
Revises: o5d6e7f8a9b0
Create Date: 2026-08-19 12:00:00.000000

Adds the values the database-restore feature writes to the audit log:
``database`` for ``audit_entity_type`` and ``restore`` for
``audit_action``.

On Postgres both are native enum types, widened in place with
ALTER TYPE ... ADD VALUE (Postgres 12+ allows this inside a
transaction as long as the new values are not used later in the same
one). On SQLite the enum names are metadata-only (columns are VARCHAR),
so there is nothing to do. No table rebuild is needed on either
dialect because the new values widen the vocabulary — existing rows are
unaffected.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "o5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ENTITY_TYPES = ("database",)
NEW_ACTIONS = ("restore",)


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
    """Widen the audit enums for restore events."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    existing = _existing_enum_values(bind, "audit_entity_type")
    for value in NEW_ENTITY_TYPES:
        if value not in existing:
            op.execute(f"ALTER TYPE audit_entity_type ADD VALUE '{value}'")

    existing = _existing_enum_values(bind, "audit_action")
    for value in NEW_ACTIONS:
        if value not in existing:
            op.execute(f"ALTER TYPE audit_action ADD VALUE '{value}'")


def downgrade() -> None:
    """Nothing to revert.

    Postgres cannot drop values from an enum type without a rebuild,
    and leftover unused values are harmless (same stance as the
    ``user_status`` 'pending' value kept by the admin-auth rework).
    """
