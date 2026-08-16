"""update_attendance_status_enum

Revision ID: g7b8c9d0e1f2
Revises: 7a8b9c0d1e2f
Create Date: 2026-08-13 00:00:00.000000

Replaces the attendance_status enum with the nine operational reporting
categories. The previous values ``present`` and ``absent`` are retained
unchanged; ``excused`` and any stray legacy values (e.g. ``unknown`` from
the original schema) are remapped to ``absent`` (the model default) and a
warning is logged per the issue's unmapped-value rule.

The downgrade is intentionally a no-op on data: once ``excused`` /
``unknown`` rows have been collapsed into ``absent`` they cannot be
reconstructed. The downgrade only restores the column type to the legacy
enum shape so the schema is reversible even though the data is not.
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "7a8b9c0d1e2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

NEW_STATUSES = (
    "present",
    "absent",
    "time_off",
    "mc",
    "yet_to_inpro",
    "outpro",
    "reporting_sick",
    "late",
    "att_out",
)
LEGACY_STATUSES = ("present", "absent", "excused", "unknown")


def upgrade() -> None:
    """Remap legacy attendance statuses, then widen the enum constraint."""
    # Capture how many rows are about to be remapped so we can warn.
    bind = op.get_bind()
    remapped = bind.execute(
        sa.text(
            "SELECT status, COUNT(*) FROM attendance_records "
            "WHERE status NOT IN ('present', 'absent') "
            "GROUP BY status"
        )
    ).all()

    # Step 1: remap legacy values to 'absent'. The old CHECK constraint
    # still permits these values so the UPDATE succeeds.
    op.execute(
        "UPDATE attendance_records SET status = 'absent' "
        "WHERE status NOT IN ('present', 'absent')"
    )

    for status_val, count in remapped:
        logger.warning(
            "Migrated %d attendance_records rows from legacy status %r to 'absent'",
            count,
            status_val,
        )

    # Step 2: widen the enum.
    if bind.dialect.name == "postgresql":
        # Native enum: add the new values in place (old values retained;
        # 'excused'/'unknown' remain as harmless leftovers after the remap
        # above). Postgres 12+ allows ADD VALUE inside a transaction as
        # long as the new values are not used later in the same one.
        existing = {
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'attendance_status'"
                )
            ).all()
        }
        for value in NEW_STATUSES:
            if value not in existing:
                # value comes from the hardcoded NEW_STATUSES tuple
                op.execute(f"ALTER TYPE attendance_status ADD VALUE '{value}'")
    else:
        # SQLite stores sa.Enum as VARCHAR with a CHECK constraint;
        # batch_alter_table rebuilds the table with the new column type.
        # render_as_batch=True is set in env.py for both online/offline.
        with op.batch_alter_table("attendance_records", schema=None) as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(*LEGACY_STATUSES, name="attendance_status"),
                type_=sa.Enum(*NEW_STATUSES, name="attendance_status"),
                existing_nullable=False,
                existing_server_default=None,
            )


def downgrade() -> None:
    """Revert the enum shape (data is NOT reversible — see module docstring)."""
    with op.batch_alter_table("attendance_records", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*NEW_STATUSES, name="attendance_status"),
            type_=sa.Enum(*LEGACY_STATUSES, name="attendance_status"),
            existing_nullable=False,
            existing_server_default=None,
        )
