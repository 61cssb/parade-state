"""rename_callup_to_inpro_status

Revision ID: v3c4d5e6f7a8
Revises: u2b3c4d5e6f7
Create Date: 2026-08-24 00:00:00.000

Replaces the callup-decision vocabulary (``callup_status``: Called Up /
Deferred / Disrupted / MR / Age Limit / Other) with the 3-value inpro
lifecycle (``inpro_status``: inproed / yet_to_inpro / deferred) — issue 32.

Data mapping:

- ``Called Up``            → ``yet_to_inpro``
- ``Deferred``             → ``deferred``
- ``Disrupted`` / ``MR`` / ``Age Limit`` / ``Other`` (and any stray legacy
  value) → ``yet_to_inpro`` with ``"Previously: <old value>"`` appended to
  ``personnel.remarks`` so the decision survives as a remark.

Because values are *removed*, PostgreSQL needs a native-enum rebuild (new
``personnel_inpro_status`` type + column swap + old type drop) rather than
in-place ADD VALUE; SQLite rebuilds via batch_alter_table. Per-value remap
counts are logged (precedent g7b8c9d0e1f2).

The downgrade is intentionally best-effort on data: ``inproed`` collapses
to ``Called Up`` (the distinction did not exist in the old vocabulary) and
the appended ``Previously:`` remarks are NOT removed. The schema shape
(column name, enum values, index) is fully restored.

Both directions are shape-guarded: a database whose personnel table is
already in the target shape but version-stamped older (the post-restore
upgrade flow can produce this — a restore dump stamped at the parent
revision) is detected via column inspection and skipped as a no-op.
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "u2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

NEW_STATUSES = ("inproed", "yet_to_inpro", "deferred")
OLD_STATUSES = ("Called Up", "Deferred", "Disrupted", "MR", "Age Limit", "Other")


def _personnel_columns(bind) -> set[str]:
    """Names of the personnel table's current columns (dialect-agnostic)."""
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("personnel")}


def _log_remap_counts(bind) -> None:
    """Warn with per-value row counts before the callup→inpro remap."""
    counts = bind.execute(
        sa.text(
            "SELECT callup_status, COUNT(*) FROM personnel "
            "GROUP BY callup_status"
        )
    ).all()
    mapping = {
        "Called Up": "yet_to_inpro",
        "Deferred": "deferred",
    }
    for status_val, count in counts:
        target = mapping.get(status_val, "yet_to_inpro (+remark)")
        logger.warning(
            "Migrated %d personnel rows from callup_status %r to inpro_status %r",
            count,
            status_val,
            target,
        )


def upgrade() -> None:
    """Rename callup_status → inpro_status and remap the vocabulary."""
    bind = op.get_bind()

    # Shape guard: a database already carrying inpro_status but version-
    # stamped older (create_all-built DB re-stamped by the restore flow)
    # has nothing to migrate.
    columns = _personnel_columns(bind)
    if "inpro_status" in columns and "callup_status" not in columns:
        logger.warning(
            "personnel already has inpro_status (no callup_status) — "
            "skipping the rename migration as a no-op"
        )
        return

    # Step 1: capture remap counts for the log (before any data changes).
    _log_remap_counts(bind)

    # Step 2: append "Previously: <value>" remarks while the old column
    # still exists (both dialects; works on empty-string remarks too).
    op.execute(
        "UPDATE personnel SET remarks = "
        "CASE WHEN remarks IS NULL OR remarks = '' "
        "THEN 'Previously: ' || callup_status "
        "ELSE remarks || '; Previously: ' || callup_status END "
        "WHERE callup_status NOT IN ('Called Up', 'Deferred')"
    )

    # Step 3: schema swap — new column/type in, old column/type out.
    if bind.dialect.name == "postgresql":
        # Native-enum rebuild: values are removed, so ADD VALUE cannot be
        # used. Create the new type, add the column (NOT NULL default fills
        # every row with the model default), remap, then drop the old
        # column + type.
        op.execute(
            "CREATE TYPE personnel_inpro_status AS ENUM "
            "('inproed', 'yet_to_inpro', 'deferred')"
        )
        op.execute(
            "ALTER TABLE personnel ADD COLUMN inpro_status "
            "personnel_inpro_status NOT NULL DEFAULT 'yet_to_inpro'"
        )
        op.execute(
            "UPDATE personnel SET inpro_status = 'deferred' "
            "WHERE callup_status = 'Deferred'"
        )
        op.drop_index("ix_personnel_callup_status", table_name="personnel")
        op.drop_column("personnel", "callup_status")
        op.execute("DROP TYPE personnel_callup_status")
        op.create_index(
            "ix_personnel_inpro_status", "personnel", ["inpro_status"]
        )
    else:
        # SQLite stores sa.Enum as VARCHAR + CHECK; batch_alter_table
        # rebuilds the table. render_as_batch=True is set in env.py.
        with op.batch_alter_table("personnel", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "inpro_status",
                    sa.Enum(*NEW_STATUSES, name="personnel_inpro_status"),
                    nullable=False,
                    server_default="yet_to_inpro",
                )
            )
        # NOT NULL default filled yet_to_inpro; remap the Deferred rows.
        op.execute(
            "UPDATE personnel SET inpro_status = 'deferred' "
            "WHERE callup_status = 'Deferred'"
        )
        with op.batch_alter_table("personnel", schema=None) as batch_op:
            batch_op.drop_index("ix_personnel_callup_status")
            batch_op.drop_column("callup_status")
            batch_op.create_index("ix_personnel_inpro_status", ["inpro_status"])


def downgrade() -> None:
    """Restore the callup_status shape (data is lossy — see docstring)."""
    bind = op.get_bind()

    # Shape guard (mirror of the upgrade guard): nothing to restore.
    columns = _personnel_columns(bind)
    if "callup_status" in columns and "inpro_status" not in columns:
        logger.warning(
            "personnel already has callup_status (no inpro_status) — "
            "skipping the downgrade as a no-op"
        )
        return

    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE TYPE personnel_callup_status AS ENUM "
            "('Called Up', 'Deferred', 'Disrupted', 'MR', 'Age Limit', 'Other')"
        )
        op.execute(
            "ALTER TABLE personnel ADD COLUMN callup_status "
            "personnel_callup_status NOT NULL DEFAULT 'Called Up'"
        )
        op.execute(
            "UPDATE personnel SET callup_status = 'Deferred' "
            "WHERE inpro_status = 'deferred'"
        )
        op.drop_index("ix_personnel_inpro_status", table_name="personnel")
        op.drop_column("personnel", "inpro_status")
        op.execute("DROP TYPE personnel_inpro_status")
        op.create_index(
            "ix_personnel_callup_status", "personnel", ["callup_status"]
        )
    else:
        with op.batch_alter_table("personnel", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "callup_status",
                    sa.Enum(*OLD_STATUSES, name="personnel_callup_status"),
                    nullable=False,
                    server_default="Called Up",
                )
            )
        op.execute(
            "UPDATE personnel SET callup_status = 'Deferred' "
            "WHERE inpro_status = 'deferred'"
        )
        with op.batch_alter_table("personnel", schema=None) as batch_op:
            batch_op.drop_index("ix_personnel_inpro_status")
            batch_op.drop_column("inpro_status")
            batch_op.create_index("ix_personnel_callup_status", ["callup_status"])
