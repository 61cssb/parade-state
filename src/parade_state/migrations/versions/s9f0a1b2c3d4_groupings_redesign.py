"""groupings redesign (issue 26)

Revision ID: s9f0a1b2c3d4
Revises: r8e9f0a1b2c3
Create Date: 2026-08-20 00:00:00.000000

Replaces the unplanned groupings implementation (modes, status
lifecycle, validity windows, overrides, notes, exclusions, per-grouping
user access / subunit scoping) with the planned model: a labelled set of
group enums on a nominal roll, memberships, and per-serviceman
checkbox/remarks. Groupings no longer interact with attendance.

FEATURE_GROUPING has been default-off throughout — old grouping data is
disposable, so this is a clean drop-and-recreate, not a data migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "s9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "r8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the old grouping tables, create the redesigned ones."""
    bind = op.get_bind()

    # Children first so FK constraints are satisfied on every dialect.
    op.drop_table("grouping_personnel_overrides")
    op.drop_table("grouping_notes")
    op.drop_table("grouping_personnel_exclusions")
    op.drop_table("grouping_user_accesses")
    op.drop_table("user_subunit_scopes")
    op.drop_table("groupings")

    # Postgres enum types used only by the old groupings table.
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS grouping_mode")
        op.execute("DROP TYPE IF EXISTS grouping_status")

    op.create_table(
        "groupings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("nominal_roll_id", sa.String(length=36), nullable=False),
        sa.Column("multiple_membership", sa.Boolean(), nullable=False),
        sa.Column("allow_ungrouped", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["nominal_roll_id"], ["nominal_rolls.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nominal_roll_id", "label", name="uq_groupings_nr_label"
        ),
    )
    op.create_index("ix_groupings_id", "groupings", ["id"], unique=False)
    op.create_index("ix_groupings_label", "groupings", ["label"], unique=False)
    op.create_index(
        "ix_groupings_nominal_roll_id", "groupings", ["nominal_roll_id"], unique=False
    )

    op.create_table(
        "grouping_groups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("grouping_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["grouping_id"], ["groupings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grouping_id", "label", name="uq_grouping_group_label"),
    )
    op.create_index("ix_grouping_groups_id", "grouping_groups", ["id"], unique=False)
    op.create_index(
        "ix_grouping_groups_grouping_id", "grouping_groups", ["grouping_id"], unique=False
    )

    op.create_table(
        "grouping_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("grouping_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["grouping_id"], ["groupings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["grouping_groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grouping_id",
            "personnel_id",
            "group_id",
            name="uq_grouping_membership",
        ),
    )
    op.create_index(
        "ix_grouping_memberships_id", "grouping_memberships", ["id"], unique=False
    )
    op.create_index(
        "ix_grouping_memberships_grouping_id",
        "grouping_memberships",
        ["grouping_id"],
        unique=False,
    )
    op.create_index(
        "ix_grouping_memberships_group_id",
        "grouping_memberships",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        "ix_grouping_memberships_personnel_id",
        "grouping_memberships",
        ["personnel_id"],
        unique=False,
    )

    op.create_table(
        "grouping_member_state",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("grouping_id", sa.String(length=36), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=False),
        sa.Column("checkbox", sa.Boolean(), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["grouping_id"], ["groupings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grouping_id", "personnel_id", name="uq_grouping_member_state"
        ),
    )
    op.create_index(
        "ix_grouping_member_state_id", "grouping_member_state", ["id"], unique=False
    )
    op.create_index(
        "ix_grouping_member_state_grouping_id",
        "grouping_member_state",
        ["grouping_id"],
        unique=False,
    )
    op.create_index(
        "ix_grouping_member_state_personnel_id",
        "grouping_member_state",
        ["personnel_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the redesigned grouping tables.

    The pre-redesign schema is not recreated — the old implementation was
    replaced wholesale (issue 26); downgrading past this point requires a
    database restore.
    """
    op.drop_table("grouping_member_state")
    op.drop_table("grouping_memberships")
    op.drop_table("grouping_groups")
    op.drop_table("groupings")
