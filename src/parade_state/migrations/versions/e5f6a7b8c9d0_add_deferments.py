"""add_deferments

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add personnel.callup_status and create deferments table.

    ``callup_status`` defaults to ``"Called Up"`` so every existing estab row
    (personnel record) is treated as called up until a deferment says otherwise.
    """
    # --- Personnel.callup_status ---
    op.add_column(
        "personnel",
        sa.Column(
            "callup_status",
            sa.Enum(
                "Called Up",
                "Not Called Up",
                "Deferred",
                name="personnel_callup_status",
            ),
            nullable=False,
            server_default="Called Up",
        ),
    )
    op.create_index(
        "ix_personnel_callup_status", "personnel", ["callup_status"]
    )

    # --- Deferments table ---
    op.create_table(
        "deferments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=False),
        sa.Column("rank_name", sa.String(length=255), nullable=False),
        sa.Column("sub_unit", sa.String(length=255), nullable=True),
        sa.Column(
            "reason",
            sa.Enum(
                "Honeymoon",
                "Work",
                "Full-time studies",
                "Other",
                "Medical Grounds",
                "Examination",
                "New employment",
                "Special employment",
                "Compassionate",
                "Childbirth",
                "Part-time studies",
                "Newly Established Business (Local)",
                name="deferment_reason",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "Approved",
                "Withdrawn",
                "Rejected",
                "To Resubmit",
                "Time off arrangement",
                "Pending action",
                "Not called up",
                "Do not call up",
                name="deferment_status",
            ),
            nullable=False,
            server_default="Pending action",
        ),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("oc_updates", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deferments_id", "deferments", ["id"], unique=False
    )
    op.create_index(
        "ix_deferments_personnel_id", "deferments", ["personnel_id"], unique=False
    )
    op.create_index(
        "ix_deferments_status", "deferments", ["status"], unique=False
    )
    op.create_index(
        "ix_deferments_updated_at", "deferments", ["updated_at"], unique=False
    )


def downgrade() -> None:
    """Drop deferments table and personnel.callup_status."""
    op.drop_index("ix_deferments_updated_at", table_name="deferments")
    op.drop_index("ix_deferments_status", table_name="deferments")
    op.drop_index("ix_deferments_personnel_id", table_name="deferments")
    op.drop_index("ix_deferments_id", table_name="deferments")
    op.drop_table("deferments")
    op.drop_index("ix_personnel_callup_status", table_name="personnel")
    op.drop_column("personnel", "callup_status")
