"""rename estabs to nominal_rolls

Revision ID: 7a8b9c0d1e2f
Revises: f6a7b8c9d0e1
Create Date: 2026-08-13 00:00:00.000000

Renames the ``estabs`` table to ``nominal_rolls`` and all FK columns that
reference it (``personnel.estab_id``, ``deployments.estab_id``,
``column_metadata.estab_id``, ``csv_uploads.estab_id``), as part of the
"Estab -> Nominal Roll" terminology cutover. Also adds a nullable ``remarks``
TEXT column for general comments.

The enum type ``estab_status`` is renamed to ``nominal_roll_status`` on
Postgres; on SQLite the enum "name" is metadata-only (column is VARCHAR), so
no schema change is needed there.

Audit log rows historically logged with entity_type='estab' are intentionally
NOT rewritten - they are immutable history. New actions log as
entity_type='nominal_roll'.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7a8b9c0d1e2f"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename estabs -> nominal_rolls; add remarks column."""
    bind = op.get_bind()

    # 1. Rename the main table.
    op.rename_table("estabs", "nominal_rolls")

    # 2. Add remarks column.
    op.add_column(
        "nominal_rolls",
        sa.Column("remarks", sa.Text(), nullable=True),
    )

    # 3. Rename indexes on nominal_rolls.
    op.drop_index("ix_estabs_id", table_name="nominal_rolls")
    op.create_index("ix_nominal_rolls_id", "nominal_rolls", ["id"], unique=False)
    op.drop_index("ix_estabs_caa", table_name="nominal_rolls")
    op.create_index("ix_nominal_rolls_caa", "nominal_rolls", ["caa"], unique=True)
    op.drop_index("ix_estabs_csv_hash", table_name="nominal_rolls")
    op.create_index("ix_nominal_rolls_csv_hash", "nominal_rolls", ["csv_hash"], unique=False)
    op.drop_index("ix_estabs_label", table_name="nominal_rolls")
    op.create_index("ix_nominal_rolls_label", "nominal_rolls", ["label"], unique=True)

    # 4. Rename FK columns on related tables.
    #    SQLite 3.25+ and Postgres both update indexes/constraints that
    #    reference the renamed column automatically.
    op.alter_column(
        "personnel",
        "estab_id",
        new_column_name="nominal_roll_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "deployments",
        "estab_id",
        new_column_name="nominal_roll_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "column_metadata",
        "estab_id",
        new_column_name="nominal_roll_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "csv_uploads",
        "estab_id",
        new_column_name="nominal_roll_id",
        existing_type=sa.String(36),
    )

    # 5. Rename personnel index + unique constraint (column was renamed in step 4).
    op.drop_index("ix_personnel_estab_id", table_name="personnel")
    op.create_index(
        "ix_personnel_nominal_roll_id", "personnel", ["nominal_roll_id"], unique=False
    )
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.drop_constraint("uq_personnel_estab_short_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_personnel_nominal_roll_short_id",
            ["nominal_roll_id", "short_id"],
        )

    # 6. Rename column_metadata unique constraint.
    with op.batch_alter_table("column_metadata", schema=None) as batch_op:
        batch_op.drop_constraint("unique_estab_column", type_="unique")
        batch_op.create_unique_constraint(
            "unique_nominal_roll_column",
            ["nominal_roll_id", "original_name"],
        )

    # 7. Rename Postgres enum types (no-op on SQLite - enum names are metadata,
    #    and the audit_entity_type column is plain VARCHAR so old rows keep
    #    their 'estab' string as-is).
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE estab_status RENAME TO nominal_roll_status")
        op.execute(
            "ALTER TYPE audit_entity_type RENAME VALUE 'estab' TO 'nominal_roll'"
        )


def downgrade() -> None:
    """Reverse the rename."""
    bind = op.get_bind()

    # 1. Reverse enum rename (Postgres only).
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE audit_entity_type RENAME VALUE 'nominal_roll' TO 'estab'"
        )
        op.execute("ALTER TYPE nominal_roll_status RENAME TO estab_status")

    # 2. Reverse column_metadata constraint.
    with op.batch_alter_table("column_metadata", schema=None) as batch_op:
        batch_op.drop_constraint("unique_nominal_roll_column", type_="unique")
        batch_op.create_unique_constraint(
            "unique_estab_column",
            ["nominal_roll_id", "original_name"],
        )

    # 3. Reverse personnel index + constraint.
    op.drop_index("ix_personnel_nominal_roll_id", table_name="personnel")
    with op.batch_alter_table("personnel", schema=None) as batch_op:
        batch_op.drop_constraint("uq_personnel_nominal_roll_short_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_personnel_estab_short_id",
            ["nominal_roll_id", "short_id"],
        )

    # 4. Rename FK columns back.
    op.alter_column(
        "csv_uploads",
        "nominal_roll_id",
        new_column_name="estab_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "column_metadata",
        "nominal_roll_id",
        new_column_name="estab_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "deployments",
        "nominal_roll_id",
        new_column_name="estab_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "personnel",
        "nominal_roll_id",
        new_column_name="estab_id",
        existing_type=sa.String(36),
    )

    # Personnel index now references the renamed-back column.
    op.create_index("ix_personnel_estab_id", "personnel", ["estab_id"], unique=False)

    # 5. Drop nominal_rolls indexes (created in upgrade step 3).
    op.drop_index("ix_nominal_rolls_label", table_name="nominal_rolls")
    op.drop_index("ix_nominal_rolls_csv_hash", table_name="nominal_rolls")
    op.drop_index("ix_nominal_rolls_caa", table_name="nominal_rolls")
    op.drop_index("ix_nominal_rolls_id", table_name="nominal_rolls")

    # 6. Drop remarks column.
    op.drop_column("nominal_rolls", "remarks")

    # 7. Rename table back.
    op.rename_table("nominal_rolls", "estabs")

    # 8. Recreate estabs indexes.
    op.create_index("ix_estabs_id", "estabs", ["id"], unique=False)
    op.create_index("ix_estabs_caa", "estabs", ["caa"], unique=True)
    op.create_index("ix_estabs_csv_hash", "estabs", ["csv_hash"], unique=False)
    op.create_index("ix_estabs_label", "estabs", ["label"], unique=True)
