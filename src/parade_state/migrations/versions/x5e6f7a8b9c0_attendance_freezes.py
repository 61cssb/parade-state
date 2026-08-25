"""attendance freezes

Revision ID: x5e6f7a8b9c0
Revises: w4d5e6f7a8b9
Create Date: 2026-08-25 00:00:00.000000

Adds the ``attendance_freezes`` table (issue 35): one row per frozen
(nominal roll, date). Row presence = frozen — admins get read-only
attendance for that day (403 on writes), super-admins keep editing.
Unfreezing deletes the row; purging the NR cascades the freezes away.

Also widens ``audit_action`` with ``attendance_freeze`` — the action both
freeze and unfreeze write (the audit description names the direction),
entity ``nominal_roll``. On Postgres the native enum is widened in place
with ALTER TYPE ... ADD VALUE (precedent p6e7f8a9b0c1); on SQLite enum
names are metadata-only (columns are VARCHAR), so there is nothing to do.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "x5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "w4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ACTIONS = ("attendance_freeze",)


def _existing_enum_values(bind, type_name: str) -> set[str]:
    """Labels currently present in a native Postgres enum type."""
    return {
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :type_name"
            ),
            {"type_name": type_name},
        ).all()
    }


def upgrade() -> None:
    """Create attendance_freezes and widen audit_action."""
    op.create_table(
        "attendance_freezes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("nominal_roll_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["nominal_roll_id"], ["nominal_rolls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nominal_roll_id", "date", name="uq_attendance_freezes_nr_date"
        ),
    )
    op.create_index(
        "ix_attendance_freezes_id", "attendance_freezes", ["id"], unique=False
    )
    op.create_index(
        "ix_attendance_freezes_nominal_roll_id",
        "attendance_freezes",
        ["nominal_roll_id"],
        unique=False,
    )
    op.create_index(
        "ix_attendance_freezes_date", "attendance_freezes", ["date"], unique=False
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    existing = _existing_enum_values(bind, "audit_action")
    for value in NEW_ACTIONS:
        if value not in existing:
            op.execute(f"ALTER TYPE audit_action ADD VALUE '{value}'")


def downgrade() -> None:
    """Drop attendance_freezes; keep the widened audit_action.

    Postgres cannot drop values from an enum type without a rebuild, and
    leftover unused values are harmless (same stance as p6e7f8a9b0c1).
    """
    op.drop_index("ix_attendance_freezes_date", table_name="attendance_freezes")
    op.drop_index(
        "ix_attendance_freezes_nominal_roll_id", table_name="attendance_freezes"
    )
    op.drop_index("ix_attendance_freezes_id", table_name="attendance_freezes")
    op.drop_table("attendance_freezes")
