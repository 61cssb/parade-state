"""add user_subunit_assignments

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-08-14 00:00:00.000000

Introduces NR-scoped Subunit-1 attendance access (issue #4 PR 2): each row
grants a user attendance-update rights for one ``sub_unit_1`` on one Nominal
Roll. ``super_admin`` bypasses; deny-by-default for everyone else.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "j0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_subunit_assignments table."""

    op.create_table(
        "user_subunit_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("nominal_roll_id", sa.String(length=36), nullable=False),
        sa.Column("sub_unit_1", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["nominal_roll_id"],
            ["nominal_rolls.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "nominal_roll_id",
            "sub_unit_1",
            name="uq_user_subunit_assignment",
        ),
    )
    op.create_index(
        "ix_user_subunit_assignments_id",
        "user_subunit_assignments",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_user_subunit_assignments_user_id",
        "user_subunit_assignments",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_subunit_assignments_nominal_roll_id",
        "user_subunit_assignments",
        ["nominal_roll_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop user_subunit_assignments."""
    op.drop_index(
        "ix_user_subunit_assignments_nominal_roll_id",
        table_name="user_subunit_assignments",
    )
    op.drop_index(
        "ix_user_subunit_assignments_user_id",
        table_name="user_subunit_assignments",
    )
    op.drop_index(
        "ix_user_subunit_assignments_id", table_name="user_subunit_assignments"
    )
    op.drop_table("user_subunit_assignments")
