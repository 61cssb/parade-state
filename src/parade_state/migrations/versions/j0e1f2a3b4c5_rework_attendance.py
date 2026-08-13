"""rework attendance

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-08-14 00:00:00.000000

Reworks attendance to attach to a Nominal Roll / Tagging scope with hardcoded
AM/PM slots, and drops the user-managed Session model.

Upgrade path:
1. Create ``attendance_scope`` (1:1 with NR; the active scope) and
   ``attendance`` (one row per personnel/day, AM+PM columns) tables.
2. Migrate existing ``attendance_records`` into ``attendance`` by joining
   through ``sessions`` (date + AM/PM type) and ``deployments`` (NR id), then
   merging the AM and PM rows for each (personnel, date) pair.
3. Log unmappable rows (orphaned attendance, missing session/deployment) to
   stdout with counts.
4. Drop ``attendance_records`` and ``sessions``.

The AM/PM merge is non-reversible: ``downgrade`` recreates empty legacy tables
for schema continuity but cannot un-merge AM/PM back into session rows.
"""

import sys
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "i9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Re-declared status vocabulary (must match parade_state.models.attendance).
ATTENDANCE_STATUSES = (
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


def upgrade() -> None:
    """Create new attendance tables, migrate data, drop legacy tables."""

    bind = op.get_bind()

    # --- attendance_scope ---
    op.create_table(
        "attendance_scope",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("nominal_roll_id", sa.String(length=36), nullable=False),
        sa.Column("tagging_id", sa.String(length=36), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("activated_by", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["nominal_roll_id"],
            ["nominal_rolls.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tagging_id"], ["taggings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nominal_roll_id", name="uq_attendance_scope_nominal_roll_id"),
    )
    op.create_index(
        "ix_attendance_scope_id", "attendance_scope", ["id"], unique=False
    )
    op.create_index(
        "ix_attendance_scope_nominal_roll_id",
        "attendance_scope",
        ["nominal_roll_id"],
        unique=False,
    )
    op.create_index(
        "ix_attendance_scope_tagging_id",
        "attendance_scope",
        ["tagging_id"],
        unique=False,
    )

    # --- attendance ---
    op.create_table(
        "attendance",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=False),
        sa.Column("nominal_roll_id", sa.String(length=36), nullable=False),
        sa.Column("tagging_id", sa.String(length=36), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "status_am",
            sa.Enum(*ATTENDANCE_STATUSES, name="attendance_status"),
            nullable=False,
        ),
        sa.Column("remarks_am", sa.Text(), nullable=True),
        sa.Column(
            "status_pm",
            sa.Enum(*ATTENDANCE_STATUSES, name="attendance_status"),
            nullable=False,
        ),
        sa.Column("remarks_pm", sa.Text(), nullable=True),
        sa.Column("notes_snapshot", sa.Text(), nullable=True),
        sa.Column("unit_snapshot", sa.String(length=255), nullable=True),
        sa.Column("sub_unit_1_snapshot", sa.String(length=255), nullable=True),
        sa.Column("sub_unit_2_snapshot", sa.String(length=255), nullable=True),
        sa.Column("sub_unit_3_snapshot", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=False),
        sa.Column("last_edit_at", sa.DateTime(), nullable=True),
        sa.Column("last_edit_by", sa.String(length=36), nullable=True),
        sa.Column("is_retroactive_edit", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["nominal_roll_id"],
            ["nominal_rolls.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tagging_id"], ["taggings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["last_edit_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "personnel_id", "date", name="uq_attendance_personnel_date"
        ),
    )
    op.create_index(
        "ix_attendance_id", "attendance", ["id"], unique=False
    )
    op.create_index(
        "ix_attendance_personnel_id", "attendance", ["personnel_id"], unique=False
    )
    op.create_index(
        "ix_attendance_nominal_roll_id",
        "attendance",
        ["nominal_roll_id"],
        unique=False,
    )
    op.create_index(
        "ix_attendance_tagging_id", "attendance", ["tagging_id"], unique=False
    )
    op.create_index(
        "ix_attendance_date", "attendance", ["date"], unique=False
    )

    # --- data migration ---
    _migrate_attendance_data(bind)

    # --- drop legacy tables ---
    op.drop_table("attendance_records")
    op.drop_table("sessions")


def _migrate_attendance_data(bind) -> None:
    """Merge legacy attendance_records (per AM/PM session) into attendance rows.

    Groups by (personnel_id, date); merges AM and PM slots. Rows whose session
    or deployment cannot be resolved are counted and dropped with a log line.
    """
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "attendance_records" not in existing_tables or "sessions" not in existing_tables:
        # Nothing to migrate (fresh install or already migrated).
        print(
            "[rework_attendance] no legacy attendance_records/sessions "
            "present — skipping data migration",
            file=sys.stderr,
        )
        return

    import uuid

    # Pull all legacy rows joined to session + deployment in one pass.
    rows = bind.execute(
        sa.text(
            """
            SELECT ar.id, ar.personnel_id, ar.deployment_id, ar.status,
                   ar.remarks, ar.notes_snapshot, ar.unit_snapshot,
                   ar.sub_unit_1_snapshot, ar.sub_unit_2_snapshot,
                   ar.sub_unit_3_snapshot, ar.created_at, ar.created_by,
                   ar.updated_at, ar.updated_by, ar.last_edit_at,
                   ar.last_edit_by, ar.is_retroactive_edit,
                   s.date AS session_date, s.session_type, d.nominal_roll_id
            FROM attendance_records ar
            LEFT JOIN sessions s ON s.id = ar.session_id
            LEFT JOIN deployments d ON d.id = ar.deployment_id
            """
        )
    ).mappings().all()

    unmapped = 0
    # key: (personnel_id, date) -> merged dict
    merged: dict[tuple, dict] = {}
    for row in rows:
        session_date = row.get("session_date")
        nominal_roll_id = row.get("nominal_roll_id")
        session_type = row.get("session_type")
        personnel_id = row.get("personnel_id")

        if (
            session_date is None
            or nominal_roll_id is None
            or session_type not in ("AM", "PM")
            or personnel_id is None
        ):
            unmapped += 1
            continue

        key = (personnel_id, str(session_date))
        slot = session_type.lower()  # "am" | "pm"
        entry = merged.setdefault(
            key,
            {
                "personnel_id": personnel_id,
                "nominal_roll_id": nominal_roll_id,
                "tagging_id": None,
                "date": session_date,
                "notes_snapshot": row.get("notes_snapshot"),
                "unit_snapshot": row.get("unit_snapshot"),
                "sub_unit_1_snapshot": row.get("sub_unit_1_snapshot"),
                "sub_unit_2_snapshot": row.get("sub_unit_2_snapshot"),
                "sub_unit_3_snapshot": row.get("sub_unit_3_snapshot"),
                # audit fields — prefer the most recent row's timestamps.
                "created_at": row.get("created_at"),
                "created_by": row.get("created_by"),
                "updated_at": row.get("updated_at"),
                "updated_by": row.get("updated_by"),
                "last_edit_at": row.get("last_edit_at"),
                "last_edit_by": row.get("last_edit_by"),
                "is_retroactive_edit": bool(row.get("is_retroactive_edit")),
                "status_am": "absent",
                "remarks_am": None,
                "status_pm": "absent",
                "remarks_pm": None,
            },
        )
        entry[f"status_{slot}"] = row.get("status") or "absent"
        entry[f"remarks_{slot}"] = row.get("remarks")
        # Keep the latest updated_at as the merged record's audit trail.
        if row.get("updated_at") and (
            entry["updated_at"] is None or row["updated_at"] > entry["updated_at"]
        ):
            entry["updated_at"] = row.get("updated_at")
            entry["updated_by"] = row.get("updated_by")
            entry["last_edit_at"] = row.get("last_edit_at")
            entry["last_edit_by"] = row.get("last_edit_by")
            entry["is_retroactive_edit"] = bool(row.get("is_retroactive_edit"))

    if unmapped:
        print(
            f"[rework_attendance] dropped {unmapped} unmappable legacy "
            "attendance_records (missing session/deployment/type)",
            file=sys.stderr,
        )

    if not merged:
        return

    # Bulk insert merged rows.
    insert_rows = []
    for entry in merged.values():
        insert_rows.append(
            {
                "id": str(uuid.uuid4()),
                "personnel_id": entry["personnel_id"],
                "nominal_roll_id": entry["nominal_roll_id"],
                "tagging_id": None,
                "date": entry["date"],
                "status_am": entry["status_am"],
                "remarks_am": entry["remarks_am"],
                "status_pm": entry["status_pm"],
                "remarks_pm": entry["remarks_pm"],
                "notes_snapshot": entry["notes_snapshot"],
                "unit_snapshot": entry["unit_snapshot"],
                "sub_unit_1_snapshot": entry["sub_unit_1_snapshot"],
                "sub_unit_2_snapshot": entry["sub_unit_2_snapshot"],
                "sub_unit_3_snapshot": entry["sub_unit_3_snapshot"],
                "created_at": entry["created_at"],
                "created_by": entry["created_by"],
                "updated_at": entry["updated_at"],
                "updated_by": entry["updated_by"],
                "last_edit_at": entry["last_edit_at"],
                "last_edit_by": entry["last_edit_by"],
                "is_retroactive_edit": entry["is_retroactive_edit"],
            }
        )

    bind.execute(
        sa.text(
            """
            INSERT INTO attendance (
                id, personnel_id, nominal_roll_id, tagging_id, date,
                status_am, remarks_am, status_pm, remarks_pm,
                notes_snapshot, unit_snapshot, sub_unit_1_snapshot,
                sub_unit_2_snapshot, sub_unit_3_snapshot,
                created_at, created_by, updated_at, updated_by,
                last_edit_at, last_edit_by, is_retroactive_edit
            ) VALUES (
                :id, :personnel_id, :nominal_roll_id, :tagging_id, :date,
                :status_am, :remarks_am, :status_pm, :remarks_pm,
                :notes_snapshot, :unit_snapshot, :sub_unit_1_snapshot,
                :sub_unit_2_snapshot, :sub_unit_3_snapshot,
                :created_at, :created_by, :updated_at, :updated_by,
                :last_edit_at, :last_edit_by, :is_retroactive_edit
            )
            """
        ),
        insert_rows,
    )
    print(
        f"[rework_attendance] migrated {len(insert_rows)} attendance rows",
        file=sys.stderr,
    )


def downgrade() -> None:
    """Best-effort: drop new tables and recreate empty legacy tables.

    The AM/PM merge performed in ``upgrade`` is non-reversible: legacy session
    rows cannot be reconstructed, so recreated tables are empty.
    """
    op.drop_index("ix_attendance_date", table_name="attendance")
    op.drop_index("ix_attendance_tagging_id", table_name="attendance")
    op.drop_index("ix_attendance_nominal_roll_id", table_name="attendance")
    op.drop_index("ix_attendance_personnel_id", table_name="attendance")
    op.drop_index("ix_attendance_id", table_name="attendance")
    op.drop_table("attendance")

    op.drop_index("ix_attendance_scope_tagging_id", table_name="attendance_scope")
    op.drop_index("ix_attendance_scope_nominal_roll_id", table_name="attendance_scope")
    op.drop_index("ix_attendance_scope_id", table_name="attendance_scope")
    op.drop_table("attendance_scope")

    # Recreate empty legacy tables for schema continuity.
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("session_type", sa.Enum("AM", "PM", name="session_type"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "closed", "finalized", name="session_status"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["deployments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deployment_id",
            "date",
            "session_type",
            name="unique_deployment_date_session_type",
        ),
    )
    op.create_table(
        "attendance_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*ATTENDANCE_STATUSES, name="attendance_status"),
            nullable=False,
        ),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("notes_snapshot", sa.Text(), nullable=True),
        sa.Column("unit_snapshot", sa.String(length=255), nullable=True),
        sa.Column("sub_unit_1_snapshot", sa.String(length=255), nullable=True),
        sa.Column("sub_unit_2_snapshot", sa.String(length=255), nullable=True),
        sa.Column("sub_unit_3_snapshot", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(length=36), nullable=False),
        sa.Column("last_edit_at", sa.DateTime(), nullable=True),
        sa.Column("last_edit_by", sa.String(length=36), nullable=True),
        sa.Column("is_retroactive_edit", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["deployments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["last_edit_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "personnel_id", name="unique_session_personnel_attendance"
        ),
    )
