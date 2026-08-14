"""tagging_1to1_with_nominal_roll

Revision ID: m3b4c5d6e7f8
Revises: l2a3b4c5d6e7
Create Date: 2026-08-14 00:00:00.000000

Promotes the Tagging overlay from many-per-NR to a strict 1:1 relationship
with NominalRoll. CSV-sourced NRs are read-only; all unit/subunit edits land
on the NR's single Tagging as TaggingEntry rows.

Upgrade path:
1. Dedup safety: for any NR with >1 tagging, pick a survivor (most recently
   ``updated_at``, tiebreak ``created_at``), re-link orphaned tagging_entries
   to the survivor, delete the surplus taggings. The demo DB has 0 taggings
   so this is a no-op there, but the migration must be safe for any state.
2. Backfill: for every NR with 0 taggings, insert one empty Tagging row.
   Label is derived from the NR's CAA date so the existing NOT NULL column
   survives the backfill; the column is made nullable immediately after.
3. Drop the ``uq_taggings_label`` unique constraint and ``ix_taggings_label``
   index — globally unique labels are no longer required under 1:1.
4. Alter ``label`` to nullable.
5. Add ``uq_taggings_nominal_roll_id`` unique constraint.

The AM/PM-style merge in step 1 is non-reversible: ``downgrade`` restores
the schema (label NOT NULL + globally unique; drop nominal_roll_id unique)
but cannot un-merge previously-merged taggings. Mirrors the precedent set in
``j0e1f2a3b4c5_rework_attendance``.
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "l2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Collapse taggings to 1:1 with NominalRoll."""

    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Step 1: Dedup — for any NR with >1 tagging, keep the most recently
    # updated one (tiebreak: created_at) as the survivor. Re-link orphaned
    # tagging_entries to the survivor, then delete the surplus taggings.
    # ------------------------------------------------------------------
    nr_ids_with_extras = bind.execute(
        sa.text(
            "SELECT nominal_roll_id FROM taggings "
            "GROUP BY nominal_roll_id HAVING COUNT(*) > 1"
        )
    ).scalars().all()

    for nr_id in nr_ids_with_extras:
        # Survivor: highest updated_at (NULLS LAST), tiebreak highest created_at.
        survivor_id = bind.execute(
            sa.text(
                "SELECT id FROM taggings WHERE nominal_roll_id = :nr_id "
                "ORDER BY "
                "CASE WHEN updated_at IS NULL THEN 1 ELSE 0 END, "
                "updated_at DESC, created_at DESC "
                "LIMIT 1"
            ),
            {"nr_id": nr_id},
        ).scalar_one()

        # Re-link orphaned entries to the survivor.
        bind.execute(
            sa.text(
                "UPDATE tagging_entries SET tagging_id = :survivor_id "
                "WHERE tagging_id IN ("
                "  SELECT id FROM taggings WHERE nominal_roll_id = :nr_id"
                ") AND tagging_id != :survivor_id"
            ),
            {"survivor_id": survivor_id, "nr_id": nr_id},
        )

        # Delete the surplus taggings (keep only the survivor).
        bind.execute(
            sa.text(
                "DELETE FROM taggings "
                "WHERE nominal_roll_id = :nr_id AND id != :survivor_id"
            ),
            {"survivor_id": survivor_id, "nr_id": nr_id},
        )

    # ------------------------------------------------------------------
    # Step 2: Backfill — for every NR with 0 taggings, insert one empty
    # Tagging row. Label is derived from CAA so the NOT NULL column
    # survives; it's made nullable in step 4.
    # ------------------------------------------------------------------
    nrs_without_tagging = bind.execute(
        sa.text(
            "SELECT n.id, n.caa, n.uploaded_by FROM nominal_rolls n "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM taggings t WHERE t.nominal_roll_id = n.id"
            ")"
        )
    ).all()

    for row in nrs_without_tagging:
        nr_id = row[0]
        caa = row[1]
        uploaded_by = row[2]
        # CAA is a date; ISO-format string is safe for the label.
        caa_str = caa.isoformat() if hasattr(caa, "isoformat") else str(caa)
        label = f"Tagging for CAA {caa_str}"
        created_at = bind.execute(sa.text("SELECT CURRENT_TIMESTAMP")).scalar_one()
        bind.execute(
            sa.text(
                "INSERT INTO taggings "
                "(id, label, nominal_roll_id, remarks, created_at, created_by) "
                "VALUES (:id, :label, :nr_id, NULL, :created_at, :created_by)"
            ),
            {
                "id": str(uuid.uuid4()),
                "label": label,
                "nr_id": nr_id,
                "created_at": created_at,
                "created_by": uploaded_by,
            },
        )

    # ------------------------------------------------------------------
    # Steps 3-5: Schema changes. batch_alter_table is required for SQLite
    # (rebuilds the table); render_as_batch=True is set in env.py.
    # ------------------------------------------------------------------
    with op.batch_alter_table("taggings", schema=None) as batch_op:
        # Drop the globally-unique label constraint + index.
        batch_op.drop_constraint("uq_taggings_label", type_="unique")
        batch_op.drop_index("ix_taggings_label")
        # Make label optional under 1:1 (NR identity is the natural key).
        batch_op.alter_column("label", existing_type=sa.String(100), nullable=True)
        # Enforce one tagging per NR.
        batch_op.create_unique_constraint(
            "uq_taggings_nominal_roll_id", ["nominal_roll_id"]
        )


def downgrade() -> None:
    """Reverse the schema changes.

    Cannot un-merge previously-merged taggings (step 1 of upgrade). The
    schema is restored to its pre-migration shape; any data that was
    consolidated stays consolidated.
    """

    with op.batch_alter_table("taggings", schema=None) as batch_op:
        batch_op.drop_constraint("uq_taggings_nominal_roll_id", type_="unique")
        batch_op.alter_column("label", existing_type=sa.String(100), nullable=False)
        batch_op.create_index("ix_taggings_label", ["label"])
        batch_op.create_unique_constraint("uq_taggings_label", ["label"])
