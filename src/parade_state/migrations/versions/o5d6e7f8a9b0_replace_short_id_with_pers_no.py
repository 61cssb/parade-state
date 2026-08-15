"""replace short_id with pers_no

Revision ID: o5d6e7f8a9b0
Revises: n4c5d6e7f8a9
Create Date: 2026-08-15 00:00:00.000000

Policy change: ``pers_no`` (the external personnel number from the CSV ``Pers``
column) is no longer sensitive and becomes the canonical cross-roll personnel
identifier, replacing the server-minted 8-char base62 ``short_id`` introduced
in ``c3d4e5f6a7b8``.

Identity semantics: one pers_no is one person globally — every row for the
same individual across rolls shares it. The DB-level guarantee mirrors the old
short_id shape: UNIQUE(nominal_roll_id, pers_no) (one row per person per roll;
multiple NULLs allowed — a NULL never matches another NULL).

Existing databases are treated as fresh-install: ``pers_no`` is added nullable
with NO backfill — the source values were dropped on parse and never stored.
Populate by re-ingesting from CSV.

Downgrade re-adds ``short_id`` backfilled with freshly minted base62 values.
The minting is inlined here (not via ``parade_state.utils.ids.short_id``,
which this change deletes) so the migration keeps working independently of
later util refactors.
"""

from typing import Sequence, Union

import secrets

import sqlalchemy as sa
from alembic import op

revision: str = "o5d6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "n4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same alphabet the old ids.short_id used (no ambiguous chars).
_BASE62_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def upgrade() -> None:
    """Add nullable pers_no (unique per roll); drop short_id."""
    op.add_column("personnel", sa.Column("pers_no", sa.String(length=20), nullable=True))

    op.drop_index("ix_personnel_short_id", table_name="personnel")
    # Batch mode for SQLite compatibility (constraint swap + column drop need
    # a table rebuild).
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_personnel_nominal_roll_short_id", type_="unique"
        )
        batch_op.drop_column("short_id")
        batch_op.create_unique_constraint(
            "uq_personnel_nominal_roll_pers_no", ["nominal_roll_id", "pers_no"]
        )

    # Create the lookup index after the rebuild so it lands on the new table.
    op.create_index("ix_personnel_pers_no", "personnel", ["pers_no"], unique=False)


def downgrade() -> None:
    """Restore short_id (minted per row); drop pers_no."""
    bind = op.get_bind()

    # Drop the pers_no index before the batch rebuild that removes its column
    # (same ordering as c3d4e5f6a7b8's upgrade).
    op.drop_index("ix_personnel_pers_no", table_name="personnel")

    op.add_column("personnel", sa.Column("short_id", sa.String(length=8), nullable=True))

    personnel = sa.table(
        "personnel",
        sa.column("id", sa.String),
        sa.column("short_id", sa.String),
    )
    seen: set[str] = set()
    rows = bind.execute(sa.select(personnel.c.id)).fetchall()
    for (row_id,) in rows:
        candidate = "".join(secrets.choice(_BASE62_ALPHABET) for _ in range(8))
        while candidate in seen:
            candidate = "".join(
                secrets.choice(_BASE62_ALPHABET) for _ in range(8)
            )
        seen.add(candidate)
        bind.execute(
            personnel.update().where(personnel.c.id == row_id).values(short_id=candidate)
        )

    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_personnel_nominal_roll_pers_no", type_="unique"
        )
        batch_op.alter_column("short_id", nullable=False)
        batch_op.create_unique_constraint(
            "uq_personnel_nominal_roll_short_id", ["nominal_roll_id", "short_id"]
        )
        batch_op.drop_column("pers_no")

    op.create_index("ix_personnel_short_id", "personnel", ["short_id"], unique=False)
