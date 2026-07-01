"""add_deployment_personnel_exclusions

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create deployment_personnel_exclusions table."""
    op.create_table(
        "deployment_personnel_exclusions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=False),
        sa.Column("excluded_at", sa.DateTime(), nullable=False),
        sa.Column("excluded_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["deployments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["excluded_by"], ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deployment_id",
            "personnel_id",
            name="unique_deployment_personnel_exclusion",
        ),
    )


def downgrade() -> None:
    """Drop deployment_personnel_exclusions table."""
    op.drop_table("deployment_personnel_exclusions")
