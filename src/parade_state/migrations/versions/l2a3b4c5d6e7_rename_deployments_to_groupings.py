"""rename deployments to groupings

Revision ID: l2a3b4c5d6e7
Revises: k1f2a3b4c5d6
Create Date: 2026-08-14 00:00:00.000000

Renames the ``deployments`` table to ``groupings`` and all related tables,
FK columns, constraints, and indexes. Adds three new columns:

- ``groupings.mode`` — ``"standard" | "adhoc" | "vehicle"`` (default
  ``"standard"``; the umbrella term covers adhoc detachments and vehicle
  manifests in addition to classic deployments).
- ``grouping_personnel_overrides.checkbox`` — free-form boolean marker per
  grouping-personnel entry (default ``false``).
- ``grouping_personnel_overrides.remarks`` — free-form text per
  grouping-personnel entry (nullable).

The enum type ``deployment_status`` is renamed to ``grouping_status`` on
Postgres; on SQLite the enum name is metadata-only (column is VARCHAR).

Audit log rows historically logged with ``entity_type='deployment'`` are
intentionally NOT rewritten — they are immutable history. New actions log as
``entity_type='grouping'``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "k1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename deployments -> groupings; add mode/checkbox/remarks columns."""
    bind = op.get_bind()

    # ========================================================================
    # 1. Rename tables.
    # ========================================================================
    op.rename_table("deployments", "groupings")
    op.rename_table("deployment_personnel_overrides", "grouping_personnel_overrides")
    op.rename_table("deployment_personnel_exclusions", "grouping_personnel_exclusions")
    op.rename_table("deployment_notes", "grouping_notes")
    op.rename_table("deployment_user_accesses", "grouping_user_accesses")

    # ========================================================================
    # 2. Rename FK columns (deployment_id -> grouping_id).
    # ========================================================================
    op.alter_column(
        "grouping_personnel_overrides",
        "deployment_id",
        new_column_name="grouping_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "grouping_personnel_exclusions",
        "deployment_id",
        new_column_name="grouping_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "grouping_notes",
        "deployment_id",
        new_column_name="grouping_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "grouping_user_accesses",
        "deployment_id",
        new_column_name="grouping_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "user_subunit_scopes",
        "deployment_id",
        new_column_name="grouping_id",
        existing_type=sa.String(36),
    )

    # ========================================================================
    # 3. Rename indexes on groupings (parent table).
    # ========================================================================
    op.drop_index("ix_deployments_id", table_name="groupings")
    op.create_index("ix_groupings_id", "groupings", ["id"], unique=False)

    # ========================================================================
    # 4. Rename indexes on child tables.
    # ========================================================================
    op.drop_index(
        "ix_deployment_personnel_overrides_id",
        table_name="grouping_personnel_overrides",
    )
    op.create_index(
        "ix_grouping_personnel_overrides_id",
        "grouping_personnel_overrides",
        ["id"],
        unique=False,
    )

    op.drop_index("ix_deployment_notes_id", table_name="grouping_notes")
    op.create_index("ix_grouping_notes_id", "grouping_notes", ["id"], unique=False)

    # grouping_user_accesses: three indexes (id, user_id, grouping_id)
    op.drop_index(
        "ix_deployment_user_accesses_deployment_id",
        table_name="grouping_user_accesses",
    )
    op.create_index(
        "ix_grouping_user_accesses_grouping_id",
        "grouping_user_accesses",
        ["grouping_id"],
        unique=False,
    )
    op.drop_index(
        "ix_deployment_user_accesses_id",
        table_name="grouping_user_accesses",
    )
    op.create_index(
        "ix_grouping_user_accesses_id",
        "grouping_user_accesses",
        ["id"],
        unique=False,
    )
    op.drop_index(
        "ix_deployment_user_accesses_user_id",
        table_name="grouping_user_accesses",
    )
    op.create_index(
        "ix_grouping_user_accesses_user_id",
        "grouping_user_accesses",
        ["user_id"],
        unique=False,
    )

    # ========================================================================
    # 5. Rename unique constraints (column names changed in step 2).
    # ========================================================================
    with op.batch_alter_table(
        "grouping_personnel_overrides", schema=None
    ) as batch_op:
        batch_op.drop_constraint(
            "unique_deployment_personnel_override", type_="unique"
        )
        batch_op.create_unique_constraint(
            "unique_grouping_personnel_override",
            ["grouping_id", "personnel_id"],
        )

    with op.batch_alter_table(
        "grouping_personnel_exclusions", schema=None
    ) as batch_op:
        batch_op.drop_constraint(
            "unique_deployment_personnel_exclusion", type_="unique"
        )
        batch_op.create_unique_constraint(
            "unique_grouping_personnel_exclusion",
            ["grouping_id", "personnel_id"],
        )

    with op.batch_alter_table("grouping_notes", schema=None) as batch_op:
        batch_op.drop_constraint(
            "unique_deployment_personnel_notes", type_="unique"
        )
        batch_op.create_unique_constraint(
            "unique_grouping_personnel_notes",
            ["grouping_id", "personnel_id"],
        )

    with op.batch_alter_table(
        "grouping_user_accesses", schema=None
    ) as batch_op:
        batch_op.drop_constraint("unique_user_deployment_access", type_="unique")
        batch_op.create_unique_constraint(
            "unique_user_grouping_access",
            ["user_id", "grouping_id"],
        )

    with op.batch_alter_table("user_subunit_scopes", schema=None) as batch_op:
        batch_op.drop_constraint("unique_user_deployment_scope", type_="unique")
        batch_op.create_unique_constraint(
            "unique_user_grouping_scope",
            [
                "user_id",
                "grouping_id",
                "unit",
                "sub_unit_1",
                "sub_unit_2",
                "sub_unit_3",
            ],
        )

    # ========================================================================
    # 6. Add new columns.
    # ========================================================================
    # groupings.mode — standard | adhoc | vehicle (default "standard")
    # PostgreSQL: create the enum type first — ALTER TABLE ADD COLUMN does
    # not create types implicitly (no-op on SQLite, which uses VARCHAR).
    sa.Enum(
        "standard",
        "adhoc",
        "vehicle",
        name="grouping_mode",
    ).create(bind, checkfirst=True)
    op.add_column(
        "groupings",
        sa.Column(
            "mode",
            sa.Enum(
                "standard",
                "adhoc",
                "vehicle",
                name="grouping_mode",
            ),
            nullable=False,
            server_default="standard",
        ),
    )

    # grouping_personnel_overrides.checkbox (bool, default false)
    op.add_column(
        "grouping_personnel_overrides",
        sa.Column(
            "checkbox",
            sa.Boolean(),
            nullable=False,
            # "false", not 0: Postgres rejects integer defaults on boolean
            # columns; SQLite accepts the keyword literal.
            server_default=sa.text("false"),
        ),
    )

    # grouping_personnel_overrides.remarks (text, nullable)
    op.add_column(
        "grouping_personnel_overrides",
        sa.Column("remarks", sa.Text(), nullable=True),
    )

    # ========================================================================
    # 7. Rename Postgres enum types (no-op on SQLite).
    # ========================================================================
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE deployment_status RENAME TO grouping_status")
        op.execute(
            "ALTER TYPE audit_entity_type RENAME VALUE 'deployment' TO 'grouping'"
        )


def downgrade() -> None:
    """Reverse the rename."""
    bind = op.get_bind()

    # 1. Reverse Postgres enum renames.
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE audit_entity_type RENAME VALUE 'grouping' TO 'deployment'"
        )
        op.execute("ALTER TYPE grouping_status RENAME TO deployment_status")

    # 2. Drop new columns.
    op.drop_column("grouping_personnel_overrides", "remarks")
    op.drop_column("grouping_personnel_overrides", "checkbox")
    op.drop_column("groupings", "mode")

    # 3. Reverse unique constraints.
    with op.batch_alter_table("user_subunit_scopes", schema=None) as batch_op:
        batch_op.drop_constraint("unique_user_grouping_scope", type_="unique")
        batch_op.create_unique_constraint(
            "unique_user_deployment_scope",
            [
                "user_id",
                "grouping_id",
                "unit",
                "sub_unit_1",
                "sub_unit_2",
                "sub_unit_3",
            ],
        )

    with op.batch_alter_table(
        "grouping_user_accesses", schema=None
    ) as batch_op:
        batch_op.drop_constraint("unique_user_grouping_access", type_="unique")
        batch_op.create_unique_constraint(
            "unique_user_deployment_access",
            ["user_id", "grouping_id"],
        )

    with op.batch_alter_table("grouping_notes", schema=None) as batch_op:
        batch_op.drop_constraint("unique_grouping_personnel_notes", type_="unique")
        batch_op.create_unique_constraint(
            "unique_deployment_personnel_notes",
            ["grouping_id", "personnel_id"],
        )

    with op.batch_alter_table(
        "grouping_personnel_exclusions", schema=None
    ) as batch_op:
        batch_op.drop_constraint(
            "unique_grouping_personnel_exclusion", type_="unique"
        )
        batch_op.create_unique_constraint(
            "unique_deployment_personnel_exclusion",
            ["grouping_id", "personnel_id"],
        )

    with op.batch_alter_table(
        "grouping_personnel_overrides", schema=None
    ) as batch_op:
        batch_op.drop_constraint(
            "unique_grouping_personnel_override", type_="unique"
        )
        batch_op.create_unique_constraint(
            "unique_deployment_personnel_override",
            ["grouping_id", "personnel_id"],
        )

    # 4. Reverse indexes.
    op.drop_index(
        "ix_grouping_user_accesses_user_id",
        table_name="grouping_user_accesses",
    )
    op.create_index(
        "ix_deployment_user_accesses_user_id",
        "grouping_user_accesses",
        ["user_id"],
        unique=False,
    )
    op.drop_index(
        "ix_grouping_user_accesses_id",
        table_name="grouping_user_accesses",
    )
    op.create_index(
        "ix_deployment_user_accesses_id",
        "grouping_user_accesses",
        ["id"],
        unique=False,
    )
    op.drop_index(
        "ix_grouping_user_accesses_grouping_id",
        table_name="grouping_user_accesses",
    )
    op.create_index(
        "ix_deployment_user_accesses_deployment_id",
        "grouping_user_accesses",
        ["grouping_id"],
        unique=False,
    )

    op.drop_index("ix_grouping_notes_id", table_name="grouping_notes")
    op.create_index(
        "ix_deployment_notes_id", "grouping_notes", ["id"], unique=False
    )

    op.drop_index(
        "ix_grouping_personnel_overrides_id",
        table_name="grouping_personnel_overrides",
    )
    op.create_index(
        "ix_deployment_personnel_overrides_id",
        "grouping_personnel_overrides",
        ["id"],
        unique=False,
    )

    op.drop_index("ix_groupings_id", table_name="groupings")
    op.create_index(
        "ix_deployments_id", "groupings", ["id"], unique=False
    )

    # 5. Reverse FK column renames.
    op.alter_column(
        "user_subunit_scopes",
        "grouping_id",
        new_column_name="deployment_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "grouping_user_accesses",
        "grouping_id",
        new_column_name="deployment_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "grouping_notes",
        "grouping_id",
        new_column_name="deployment_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "grouping_personnel_exclusions",
        "grouping_id",
        new_column_name="deployment_id",
        existing_type=sa.String(36),
    )
    op.alter_column(
        "grouping_personnel_overrides",
        "grouping_id",
        new_column_name="deployment_id",
        existing_type=sa.String(36),
    )

    # 6. Reverse table renames.
    op.rename_table("grouping_user_accesses", "deployment_user_accesses")
    op.rename_table("grouping_notes", "deployment_notes")
    op.rename_table("grouping_personnel_exclusions", "deployment_personnel_exclusions")
    op.rename_table("grouping_personnel_overrides", "deployment_personnel_overrides")
    op.rename_table("groupings", "deployments")
