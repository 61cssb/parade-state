"""add_discussions

Revision ID: t0a1b2c3d4e5
Revises: s9f0a1b2c3d4
Create Date: 2026-08-21 00:00:00.000

Creates the discussions board tables (issue 24) and widens the audit
``entity_type`` enum with ``discussion_post`` so super-admin triage actions
(category/status changes) can be audit-logged.

The enum widening follows the q7d8e9f0a1b2 pattern: PostgreSQL needs the
native-type ``ADD VALUE`` in an autocommit block (adding and using a value
in one transaction is forbidden); SQLite stores sa.Enum as VARCHAR with a
CHECK constraint, so batch_alter_table rebuilds the column with the wider
value set. The downgrade drops the tables and, on SQLite only, narrows the
CHECK back — on PostgreSQL the added enum value is retained (removing
values requires a type rebuild; leftovers are harmless).
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "t0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "s9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

LEGACY_AUDIT_ENTITY_TYPES = (
    "attendance",
    "grouping",
    "session",
    "user",
    "csv_upload",
    "nominal_roll",
    "personnel",
    "access_level",
    "column_mapping",
    "database",
)
NEW_AUDIT_ENTITY_TYPES = LEGACY_AUDIT_ENTITY_TYPES + ("discussion_post",)


def upgrade() -> None:
    """Create the discussions tables, then widen the audit entity enum."""
    # Step 1: the board tables (both dialects, plain creates).
    op.create_table(
        "discussion_posts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "author_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("category", sa.Enum("requests", "bugs", name="discussion_category"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "Open",
                "Duplicate",
                "Accepted",
                "Implemented",
                "Closed",
                name="discussion_post_status",
            ),
            server_default="Open",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_discussion_posts_author_id"), "discussion_posts", ["author_id"]
    )
    op.create_index(
        op.f("ix_discussion_posts_category"), "discussion_posts", ["category"]
    )
    op.create_index(
        op.f("ix_discussion_posts_status"), "discussion_posts", ["status"]
    )

    op.create_table(
        "discussion_comments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "post_id",
            sa.String(length=36),
            sa.ForeignKey("discussion_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_discussion_comments_post_id"),
        "discussion_comments",
        ["post_id"],
    )
    op.create_index(
        op.f("ix_discussion_comments_author_id"),
        "discussion_comments",
        ["author_id"],
    )

    # Step 2: widen audit_entity_type with 'discussion_post'. No existing
    # rows are touched — every legacy value is retained in the wider set.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        existing = {
            row[0]
            for row in bind.execute(
                sa.text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'audit_entity_type'"
                )
            ).all()
        }
        with op.get_context().autocommit_block():
            for value in NEW_AUDIT_ENTITY_TYPES:
                if value not in existing:
                    # value comes from the hardcoded NEW_AUDIT_ENTITY_TYPES
                    op.execute(
                        f"ALTER TYPE audit_entity_type ADD VALUE '{value}'"
                    )
    else:
        # SQLite: sa.Enum is VARCHAR + CHECK; rebuild the column with the
        # wider value set (render_as_batch=True is set in env.py).
        with op.batch_alter_table("audit_logs", schema=None) as batch_op:
            batch_op.alter_column(
                "entity_type",
                existing_type=sa.Enum(
                    *LEGACY_AUDIT_ENTITY_TYPES, name="audit_entity_type"
                ),
                type_=sa.Enum(
                    *NEW_AUDIT_ENTITY_TYPES, name="audit_entity_type"
                ),
                existing_nullable=False,
                existing_server_default=None,
            )


def downgrade() -> None:
    """Drop the board tables; narrow the audit enum shape on SQLite only.

    PostgreSQL keeps the 'discussion_post' enum value (removing values
    requires a type rebuild; an unused value is harmless), matching the
    q7d8e9f0a1b2 downgrade posture — its audit rows stay readable. On
    SQLite the CHECK cannot admit the value after the rebuild, so those
    rows are removed first (the feature they describe is gone).
    """
    op.drop_table("discussion_comments")
    op.drop_table("discussion_posts")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Native enum types outlive their tables on PostgreSQL — drop the
        # two this migration created so a re-upgrade does not collide
        # (DuplicateObjectError). The widened audit_entity_type stays
        # (q7 downgrade posture).
        op.execute("DROP TYPE IF EXISTS discussion_category")
        op.execute("DROP TYPE IF EXISTS discussion_post_status")
    else:
        bind.execute(
            sa.text("DELETE FROM audit_logs WHERE entity_type = 'discussion_post'")
        )
        with op.batch_alter_table("audit_logs", schema=None) as batch_op:
            batch_op.alter_column(
                "entity_type",
                existing_type=sa.Enum(
                    *NEW_AUDIT_ENTITY_TYPES, name="audit_entity_type"
                ),
                type_=sa.Enum(
                    *LEGACY_AUDIT_ENTITY_TYPES, name="audit_entity_type"
                ),
                existing_nullable=False,
                existing_server_default=None,
            )
