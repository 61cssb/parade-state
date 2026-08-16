"""add_personnel_category

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-08-13 00:00:00.000000

Adds the ``category`` column (``Officer`` / ``WOSE``) to ``personnel``,
inferred from rank at ingestion time. The column is added nullable so
existing dev DBs keep loading; the demo DB is regenerated from the fixture
CSV rather than backfilled, and every row picks up its category on the next
ingest. Application code always sets ``category`` on insert (via
``ranks.category_for_rank``), so the column is non-null in practice once
re-ingested.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable ``category`` column with an index."""
    # SQLite stores sa.Enum as VARCHAR with a CHECK constraint;
    # batch_alter_table rebuilds the table with the new column.
    # render_as_batch=True is set in env.py for both online/offline.
    # PostgreSQL needs the enum type created explicitly: batch mode emits
    # ALTER TABLE ... ADD COLUMN, which does not create types implicitly.
    sa.Enum("Officer", "WOSE", name="personnel_category").create(
        op.get_bind(), checkfirst=True
    )
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "category",
                sa.Enum("Officer", "WOSE", name="personnel_category"),
                nullable=True,
            )
        )
        batch_op.create_index("ix_personnel_category", ["category"])


def downgrade() -> None:
    """Drop the ``category`` column and its index."""
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.drop_index("ix_personnel_category")
        batch_op.drop_column("category")
