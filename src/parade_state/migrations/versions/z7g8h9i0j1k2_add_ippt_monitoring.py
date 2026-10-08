"""IPPT monitoring tables

Revision ID: z7g8h9i0j1k2
Revises: y6f7a8b9c0d1
Create Date: 2026-10-08 00:00:00.000000

Adds the six ``ippt_*`` tables of the IPPT monitoring feature
(docs/IPPT_MONITORING.md §6): the identity spine, per-file snapshots,
birthday-anchored windows, the state-observation trajectory spine,
health-screening history, and the quarantined-row reject list.

The feature is local-testing-only (FEATURE_IPPT, force-disabled in
production), but the schema migrates cleanly on both dialects like any
other table. Also widens ``audit_entity_type`` with ``ippt_snapshot``
so the upload endpoint can audit-log ingests (precedent y6f7a8b9c0d1);
on SQLite enum names are metadata-only, nothing to do.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "z7g8h9i0j1k2"
down_revision: Union[str, Sequence[str], None] = "y6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_ENTITY_TYPES = ("ippt_snapshot",)

# BigInteger PKs: bigserial on Postgres, plain integer rowid on SQLite.
BigId = sa.BigInteger().with_variant(sa.Integer, "sqlite")


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
    """Create the ippt_* tables and widen audit_entity_type."""
    op.create_table(
        "ippt_servicemen",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("rank", sa.String(length=16), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("full_name_norm", sa.String(length=255), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("sub_unit", sa.String(length=64), nullable=False),
        sa.Column("personnel_id", sa.String(length=36), nullable=True),
        sa.Column("match_status", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "match_status IN ('matched', 'unmatched', 'ambiguous')",
            name="ck_ippt_serviceman_match_status",
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ippt_servicemen_full_name_norm", "ippt_servicemen", ["full_name_norm"]
    )
    op.create_index(
        "ix_ippt_servicemen_personnel_id", "ippt_servicemen", ["personnel_id"]
    )

    op.create_table(
        "ippt_snapshots",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("file_kind", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "file_kind IN ('ippt_completed', 'ippt_failed', 'ippt_not_attempted',"
            " 'nsfit_completed', 'nsfit_in_progress', 'nsfit_not_started')",
            name="ck_ippt_snapshots_file_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_date", "file_kind", name="uq_ippt_snapshot_date_kind"
        ),
    )
    op.create_index("ix_ippt_snapshots_report_date", "ippt_snapshots", ["report_date"])

    op.create_table(
        "ippt_windows",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("serviceman_id", BigId, nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["serviceman_id"],
            ["ippt_servicemen.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serviceman_id", "window_end", name="uq_ippt_window_end"),
    )
    op.create_index("ix_ippt_windows_serviceman_id", "ippt_windows", ["serviceman_id"])

    op.create_table(
        "ippt_state_observations",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("snapshot_id", BigId, nullable=False),
        sa.Column("serviceman_id", BigId, nullable=False),
        sa.Column("window_id", BigId, nullable=True),
        sa.Column("rank", sa.String(length=16), nullable=False),
        sa.Column("sub_unit", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=48), nullable=False),
        sa.Column("ippt_status", sa.String(length=32), nullable=True),
        sa.Column("fit_sessions", sa.Integer(), nullable=True),
        sa.Column("window_close_days", sa.Integer(), nullable=True),
        sa.Column("booked", sa.Boolean(), nullable=True),
        sa.Column("ffi", sa.String(length=16), nullable=True),
        sa.Column("reminder_sent", sa.String(length=64), nullable=True),
        sa.Column("last_sent", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "state IN ('met_award', 'met_voluntary_fit', 'met_mandatory_fit',"
            " 'waived', 'failed_can_reattempt', 'voluntary_fit_in_progress',"
            " 'mandatory_fit_in_progress', 'mandatory_fit_failed_attempt',"
            " 'ippt_not_attempted', 'nsfit_not_started')",
            name="ck_ippt_observation_state",
        ),
        sa.CheckConstraint(
            "ffi IS NULL OR ffi IN ('na', 'fit', 'pending')",
            name="ck_ippt_observation_ffi",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["ippt_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["serviceman_id"], ["ippt_servicemen.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["window_id"], ["ippt_windows.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "serviceman_id",
            name="uq_ippt_observation_snapshot_serviceman",
        ),
    )
    op.create_index(
        "ix_ippt_state_observations_snapshot_id",
        "ippt_state_observations",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_ippt_state_observations_serviceman_id",
        "ippt_state_observations",
        ["serviceman_id"],
    )

    op.create_table(
        "ippt_health_screenings",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("serviceman_id", BigId, nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("screened_on", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "status IN ('na', 'fit', 'pending')", name="ck_ippt_screening_status"
        ),
        sa.ForeignKeyConstraint(
            ["serviceman_id"], ["ippt_servicemen.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "serviceman_id", "observed_on", name="uq_ippt_screening_serviceman_date"
        ),
    )
    op.create_index(
        "ix_ippt_health_screenings_serviceman_id",
        "ippt_health_screenings",
        ["serviceman_id"],
    )

    op.create_table(
        "ippt_quarantined_rows",
        sa.Column("id", BigId, autoincrement=True, nullable=False),
        sa.Column("snapshot_id", BigId, nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("raw_row", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["ippt_snapshots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ippt_quarantined_rows_snapshot_id",
        "ippt_quarantined_rows",
        ["snapshot_id"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    existing = _existing_enum_values(bind, "audit_entity_type")
    for value in NEW_ENTITY_TYPES:
        if value not in existing:
            op.execute(f"ALTER TYPE audit_entity_type ADD VALUE '{value}'")


def downgrade() -> None:
    """Drop the ippt_* tables; keep the widened audit_entity_type.

    Postgres cannot drop values from an enum type without a rebuild, and
    leftover unused values are harmless (same stance as x5e6f7a8b9c0).
    """
    op.drop_index(
        "ix_ippt_quarantined_rows_snapshot_id", table_name="ippt_quarantined_rows"
    )
    op.drop_table("ippt_quarantined_rows")
    op.drop_index(
        "ix_ippt_health_screenings_serviceman_id", table_name="ippt_health_screenings"
    )
    op.drop_table("ippt_health_screenings")
    op.drop_index(
        "ix_ippt_state_observations_serviceman_id", table_name="ippt_state_observations"
    )
    op.drop_index(
        "ix_ippt_state_observations_snapshot_id", table_name="ippt_state_observations"
    )
    op.drop_table("ippt_state_observations")
    op.drop_index("ix_ippt_windows_serviceman_id", table_name="ippt_windows")
    op.drop_table("ippt_windows")
    op.drop_index("ix_ippt_snapshots_report_date", table_name="ippt_snapshots")
    op.drop_table("ippt_snapshots")
    op.drop_index("ix_ippt_servicemen_personnel_id", table_name="ippt_servicemen")
    op.drop_index("ix_ippt_servicemen_full_name_norm", table_name="ippt_servicemen")
    op.drop_table("ippt_servicemen")
