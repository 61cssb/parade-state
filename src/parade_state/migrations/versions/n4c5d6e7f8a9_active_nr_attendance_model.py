"""active_nr_attendance_model

Revision ID: n4c5d6e7f8a9
Revises: m3b4c5d6e7f8
Create Date: 2026-08-14 00:00:00.000000

Replaces the per-NR AttendanceScope activation workflow with a single
system-wide "active for attendance" NominalRoll, and drops the
draft/confirmed NR status workflow entirely.

New model:
- ``nominal_rolls.attendance_active`` (bool) — exactly one NR is active at
  a time (application-enforced on activate). Attendance writes are only
  permitted against the active NR, always with its 1:1 tagging applied.
- The ``attendance_scope`` table is dropped; its most recently activated
  row (if any) is honoured in the backfill.
- ``attendance.tagging_id`` is dropped — under 1:1 it is derivable from
  ``nominal_roll_id``.
- ``nominal_rolls.status`` / ``confirmed_at`` / ``confirmed_by`` are
  dropped — the confirm/unconfirm workflow is gone; all NRs are equal.

Downgrade restores the schema shape only (status column values default to
'draft', the attendance_scope table is recreated empty). Dropped status /
confirmed_* data is not recoverable — mirroring the precedent set in
``j0e1f2a3b4c5_rework_attendance``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "m3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Active-NR attendance model; drop scope table, status workflow."""

    bind = op.get_bind()

    # --- 1. nominal_rolls: active-attendance columns ---
    with op.batch_alter_table("nominal_rolls", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("attendance_active", sa.Boolean(), nullable=False,
                      server_default=sa.false())
        )
        batch_op.add_column(sa.Column("attendance_activated_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("attendance_activated_by", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_nominal_rolls_attendance_activated_by",
            "users",
            ["attendance_activated_by"],
            ["id"],
        )

    # --- 2. Backfill: honour the most recently activated scope row ---
    latest_scope = bind.execute(
        sa.text(
            "SELECT nominal_roll_id, activated_at, activated_by "
            "FROM attendance_scope "
            "ORDER BY CASE WHEN activated_at IS NULL THEN 1 ELSE 0 END, "
            "activated_at DESC LIMIT 1"
        )
    ).first()
    if latest_scope is not None:
        bind.execute(
            sa.text(
                "UPDATE nominal_rolls SET attendance_active = 1, "
                "attendance_activated_at = :activated_at, "
                "attendance_activated_by = :activated_by "
                "WHERE id = :nr_id"
            ),
            {
                "activated_at": latest_scope[1],
                "activated_by": latest_scope[2],
                "nr_id": latest_scope[0],
            },
        )

    # --- 3. Drop the attendance_scope table ---
    op.drop_table("attendance_scope")

    # --- 4. attendance: drop tagging_id (derivable from nominal_roll_id) ---
    # The FK was created unnamed; batch drop_column removes it with the
    # column during the table rebuild.
    with op.batch_alter_table("attendance", schema=None) as batch_op:
        batch_op.drop_index("ix_attendance_tagging_id")
        batch_op.drop_column("tagging_id")

    # --- 5. nominal_rolls: drop the status workflow columns ---
    with op.batch_alter_table("nominal_rolls", schema=None) as batch_op:
        batch_op.drop_column("confirmed_at")
        batch_op.drop_column("confirmed_by")
        batch_op.drop_column("status")


def downgrade() -> None:
    """Restore schema shape (dropped data is not recoverable)."""

    with op.batch_alter_table("nominal_rolls", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.Enum("draft", "confirmed", "archived", name="nominal_roll_status"),
                nullable=False,
                server_default="draft",
            )
        )
        batch_op.add_column(sa.Column("confirmed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("confirmed_by", sa.String(length=36), nullable=True))

    with op.batch_alter_table("attendance", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tagging_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_attendance_tagging_id", ["tagging_id"])
        batch_op.create_foreign_key(
            "fk_attendance_tagging_id_taggings",
            "taggings",
            ["tagging_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "attendance_scope",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("nominal_roll_id", sa.String(length=36), nullable=False),
        sa.Column("tagging_id", sa.String(length=36), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("activated_by", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["nominal_roll_id"], ["nominal_rolls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tagging_id"], ["taggings.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nominal_roll_id", name="uq_attendance_scope_nominal_roll_id"
        ),
    )
    op.create_index("ix_attendance_scope_id", "attendance_scope", ["id"], unique=False)
    op.create_index(
        "ix_attendance_scope_nominal_roll_id", "attendance_scope", ["nominal_roll_id"]
    )
    op.create_index(
        "ix_attendance_scope_tagging_id", "attendance_scope", ["tagging_id"]
    )

    with op.batch_alter_table("nominal_rolls", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_nominal_rolls_attendance_activated_by", type_="foreignkey"
        )
        batch_op.drop_column("attendance_activated_by")
        batch_op.drop_column("attendance_activated_at")
        batch_op.drop_column("attendance_active")
