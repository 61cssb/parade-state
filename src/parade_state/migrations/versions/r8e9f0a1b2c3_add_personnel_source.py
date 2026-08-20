"""add_personnel_source

Revision ID: r8e9f0a1b2c3
Revises: q7d8e9f0a1b2
Create Date: 2026-08-20 00:00:00.000

Adds a nullable provenance column ``personnel.source``: NULL marks rows
created by CSV ingestion, ``"manual"`` marks rows added by a super-admin
through the "Add Serviceman" flow. Pure ``add_column`` — no constraint or
data migration; existing rows stay NULL (= CSV).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "q7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add personnel.source (nullable provenance marker)."""
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(16), nullable=True))


def downgrade() -> None:
    """Drop personnel.source."""
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.drop_column("source")
