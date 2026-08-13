"""add_label_to_estabs

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-08 00:00:00.000000

Adds a unique, nullable `label` column to `estabs`. NULLs are allowed
(SQLite and Postgres both permit multiple NULLs under UNIQUE), so existing
rows are unaffected and future unlabeled estabs still work. Once set,
labels must be unique across all estabs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "estabs",
        sa.Column("label", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_estabs_label", "estabs", ["label"], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_estabs_label", table_name="estabs")
    op.drop_column("estabs", "label")
