"""replace pers_no with short_id

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-06-25 00:00:00.000000

Drops ``pers_no`` (an opaque, sensitive external primary key — no longer imported
or stored) and introduces an 8-char base62 ``short_id`` as the cross-estab
personnel identity (shared by every row belonging to the same individual).

Each existing row is backfilled with a freshly minted ``short_id``. This migration
does NOT attempt pers_no-based person grouping: existing data is dev-only and a
one-short_id-per-row backfill is acceptable. Real cross-estab matching is handled
by the application's person-matching service on future estab ingests.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from parade_state.utils import ids

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop pers_no; add cross-estab short_id."""
    bind = op.get_bind()

    # 1. Add short_id nullable so existing rows can be backfilled.
    op.add_column("personnel", sa.Column("short_id", sa.String(length=8), nullable=True))

    # 2. Backfill each existing row with a unique short_id.
    personnel = sa.table(
        "personnel",
        sa.column("id", sa.String),
        sa.column("short_id", sa.String),
    )
    seen: set[str] = set()
    rows = bind.execute(sa.select(personnel.c.id)).fetchall()
    for (row_id,) in rows:
        sid = ids.mint_unique_short_id(lambda candidate: candidate in seen)
        seen.add(sid)
        bind.execute(
            personnel.update().where(personnel.c.id == row_id).values(short_id=sid)
        )

    # 3. Drop pers_no index (column itself dropped inside batch below).
    op.drop_index("ix_personnel_pers_no", table_name="personnel")

    # 4. Tighten short_id to NOT NULL, add unique constraint, drop pers_no.
    #    Use batch mode for SQLite compatibility (SQLite cannot ALTER COLUMN
    #    SET NOT NULL or ADD CONSTRAINT directly — batch does copy-and-move).
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.alter_column("short_id", nullable=False)
        batch_op.create_unique_constraint(
            "uq_personnel_estab_short_id", ["estab_id", "short_id"]
        )
        batch_op.drop_column("pers_no")

    # 5. Index the new short_id column.
    op.create_index("ix_personnel_short_id", "personnel", ["short_id"], unique=False)


def downgrade() -> None:
    """Restore pers_no; drop short_id."""
    op.drop_index("ix_personnel_short_id", table_name="personnel")

    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.drop_constraint("uq_personnel_estab_short_id", type_="unique")
        batch_op.add_column(
            sa.Column(
                "pers_no",
                sa.String(length=255),
                nullable=False,
                server_default="",
            )
        )
        batch_op.drop_column("short_id")

    op.create_index("ix_personnel_pers_no", "personnel", ["pers_no"], unique=False)
