"""expand_callup_status_add_remarks

Revision ID: q7d8e9f0a1b2
Revises: p6e7f8a9b0c1
Create Date: 2026-08-20 00:00:00.000

Replaces the callup_status enum ("Called Up" / "Not Called Up" / "Deferred")
with the six operational decision statuses and adds a per-person ``remarks``
free-text column. Legacy ``Not Called Up`` rows are remapped to ``Other``;
``Called Up`` and ``Deferred`` are retained unchanged.

Ordering matters: the enum is widened BEFORE the data remap because the
remap target ``Other`` does not exist in the legacy value set. On PostgreSQL
the ADD VALUE runs in an autocommit block so the subsequent UPDATE may
reference the new value within the same migration transaction.

The downgrade is intentionally best-effort on data: rows remapped to
``Other`` cannot be reliably restored to their original legacy value. On
PostgreSQL they collapse to ``Not Called Up`` (the type retains the value);
on SQLite they collapse to ``Called Up`` (the legacy CHECK cannot admit
``Not Called Up`` again before the rebuild). ``remarks`` is dropped and, on
PostgreSQL, the enum type is not shrunk (removing values requires a type
rebuild); leftover values are harmless.
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "p6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

NEW_STATUSES = (
    "Called Up",
    "Deferred",
    "Disrupted",
    "MR",
    "Age Limit",
    "Other",
)
LEGACY_STATUSES = ("Called Up", "Not Called Up", "Deferred")
_REMAP_TARGETS = ("Disrupted", "MR", "Age Limit", "Other")


def upgrade() -> None:
    """Widen the callup_status enum, remap legacy values, add remarks."""
    bind = op.get_bind()

    # Step 1: widen the enum. Every legacy value is retained in the new set,
    # so existing rows satisfy the widened constraint immediately.
    if bind.dialect.name == "postgresql":
        # Native enum: add the new values in place (old values retained).
        # ADD VALUE must run outside the migration transaction (autocommit
        # block) because the remap UPDATE below uses 'Other' — Postgres
        # forbids using a value added in the same transaction.
        existing = {
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'personnel_callup_status'"
                )
            ).all()
        }
        with op.get_context().autocommit_block():
            for value in NEW_STATUSES:
                if value not in existing:
                    # value comes from the hardcoded NEW_STATUSES tuple
                    op.execute(
                        f"ALTER TYPE personnel_callup_status ADD VALUE '{value}'"
                    )
    else:
        # SQLite stores sa.Enum as VARCHAR with a CHECK constraint;
        # batch_alter_table rebuilds the table with the new column type.
        # render_as_batch=True is set in env.py for both online/offline.
        with op.batch_alter_table("personnel", schema=None) as batch_op:
            batch_op.alter_column(
                "callup_status",
                existing_type=sa.Enum(*LEGACY_STATUSES, name="personnel_callup_status"),
                type_=sa.Enum(*NEW_STATUSES, name="personnel_callup_status"),
                existing_nullable=False,
                existing_server_default=None,
            )

    # Step 2: remap the legacy value to 'Other' (permitted by the widened
    # constraint). Capture counts first so we can warn.
    remapped = bind.execute(
        sa.text(
            "SELECT callup_status, COUNT(*) FROM personnel "
            "WHERE callup_status = 'Not Called Up' "
            "GROUP BY callup_status"
        )
    ).all()
    op.execute(
        "UPDATE personnel SET callup_status = 'Other' "
        "WHERE callup_status = 'Not Called Up'"
    )
    for status_val, count in remapped:
        logger.warning(
            "Migrated %d personnel rows from legacy callup_status %r to 'Other'",
            count,
            status_val,
        )

    # Step 3: per-person remarks column (both dialects).
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.add_column(sa.Column("remarks", sa.Text(), nullable=True))


def downgrade() -> None:
    """Revert the schema (data is NOT reversible — see module docstring)."""
    bind = op.get_bind()
    targets = ", ".join(f"'{v}'" for v in _REMAP_TARGETS)
    if bind.dialect.name == "postgresql":
        # The native type still knows 'Not Called Up'.
        op.execute(
            f"UPDATE personnel SET callup_status = 'Not Called Up' "
            f"WHERE callup_status IN ({targets})"
        )
    else:
        # The current CHECK cannot admit 'Not Called Up'; collapse to the
        # model default so the legacy-shape rebuild below succeeds.
        op.execute(
            f"UPDATE personnel SET callup_status = 'Called Up' "
            f"WHERE callup_status IN ({targets})"
        )
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.drop_column("remarks")
        # On PostgreSQL the native enum keeps the added values; this type
        # change only restores the declared shape for fresh SQLite rebuilds.
        batch_op.alter_column(
            "callup_status",
            existing_type=sa.Enum(*NEW_STATUSES, name="personnel_callup_status"),
            type_=sa.Enum(*LEGACY_STATUSES, name="personnel_callup_status"),
            existing_nullable=False,
            existing_server_default=None,
        )
