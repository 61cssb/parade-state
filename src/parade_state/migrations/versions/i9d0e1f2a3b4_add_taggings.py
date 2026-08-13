"""add_taggings

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-08-13 00:00:00.000000

Introduces the Tagging overlay: ``taggings`` (named overlay on a nominal
roll) and ``tagging_entries`` (per-person subunit remaps). Taggings never
mutate the underlying nominal roll — they are consumed downstream by
attendance / groupings to render the remapped structure.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "h8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create taggings + tagging_entries tables."""

    # --- taggings ---
    op.create_table(
        "taggings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("nominal_roll_id", sa.String(length=36), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["nominal_roll_id"],
            ["nominal_rolls.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label", name="uq_taggings_label"),
    )
    op.create_index(
        "ix_taggings_id", "taggings", ["id"], unique=False
    )
    op.create_index(
        "ix_taggings_label", "taggings", ["label"], unique=False
    )
    op.create_index(
        "ix_taggings_nominal_roll_id",
        "taggings",
        ["nominal_roll_id"],
        unique=False,
    )
    op.create_index(
        "ix_taggings_updated_at", "taggings", ["updated_at"], unique=False
    )

    # --- tagging_entries ---
    op.create_table(
        "tagging_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tagging_id", sa.String(length=36), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=False),
        sa.Column("from_unit", sa.String(length=255), nullable=True),
        sa.Column("from_sub_unit_1", sa.String(length=255), nullable=True),
        sa.Column("from_sub_unit_2", sa.String(length=255), nullable=True),
        sa.Column("from_sub_unit_3", sa.String(length=255), nullable=True),
        sa.Column("to_unit", sa.String(length=255), nullable=False),
        sa.Column("to_sub_unit_1", sa.String(length=255), nullable=True),
        sa.Column("to_sub_unit_2", sa.String(length=255), nullable=True),
        sa.Column("to_sub_unit_3", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tagging_id"], ["taggings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tagging_id", "personnel_id", name="uq_tagging_entry_person"
        ),
    )
    op.create_index(
        "ix_tagging_entries_id", "tagging_entries", ["id"], unique=False
    )
    op.create_index(
        "ix_tagging_entries_tagging_id",
        "tagging_entries",
        ["tagging_id"],
        unique=False,
    )
    op.create_index(
        "ix_tagging_entries_personnel_id",
        "tagging_entries",
        ["personnel_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop tagging_entries + taggings tables."""
    op.drop_index(
        "ix_tagging_entries_personnel_id", table_name="tagging_entries"
    )
    op.drop_index("ix_tagging_entries_tagging_id", table_name="tagging_entries")
    op.drop_index("ix_tagging_entries_id", table_name="tagging_entries")
    op.drop_table("tagging_entries")

    op.drop_index("ix_taggings_updated_at", table_name="taggings")
    op.drop_index("ix_taggings_nominal_roll_id", table_name="taggings")
    op.drop_index("ix_taggings_label", table_name="taggings")
    op.drop_index("ix_taggings_id", table_name="taggings")
    op.drop_table("taggings")
