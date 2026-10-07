"""unit dimension on subunit assignments

Revision ID: u2b3c4d5e6f7
Revises: t0a1b2c3d4e5
Create Date: 2026-08-22 00:00:00.000000

Issue #28: scope grants gain the unit dimension. A grant is now a
(unit, sub_unit_1) pair on a Nominal Roll, each column using the explicit
sentinel ``*`` for wildcard matching (never an empty string, so accidental
blanks fail validation instead of widening access):

- (U, *) — every sub-unit of unit U
- (U, S) — exactly U/S
- (*, S) — S under any unit (the pre-#28 semantics every existing row
  keeps via the column default)
- (*, *) — forbidden by a CHECK constraint

The old three-column unique constraint is replaced by the four-column one
(without it, holding S under two different units would collide). SQLite
gets the table rebuilt via batch_alter_table; PostgreSQL takes plain
ALTERs. Downgrade restores the old shape — it fails only if unit-specific
duplicates (same user/NR/sub_unit_1 under different units) exist, which
the four-column constraint permits but the old one cannot represent.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "u2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "t0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "user_subunit_assignments"
_UNIQUE_NAME = "uq_user_subunit_assignment"
_CHECK_NAME = "ck_user_subunit_assignment_not_both_wildcard"


def upgrade() -> None:
    """Add the unit column, widen the unique, add the wildcard guard."""
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(
            sa.Column(
                "unit",
                sa.String(length=255),
                nullable=False,
                server_default="*",
            )
        )
        batch.drop_constraint(_UNIQUE_NAME, type_="unique")
        batch.create_unique_constraint(
            _UNIQUE_NAME,
            ["user_id", "nominal_roll_id", "unit", "sub_unit_1"],
        )
        batch.create_check_constraint(
            _CHECK_NAME,
            "unit <> '*' OR sub_unit_1 <> '*'",
        )


def downgrade() -> None:
    """Restore the pre-#28 shape (unique without unit, no unit column)."""
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CHECK_NAME, type_="check")
        batch.drop_constraint(_UNIQUE_NAME, type_="unique")
        batch.create_unique_constraint(
            _UNIQUE_NAME,
            ["user_id", "nominal_roll_id", "sub_unit_1"],
        )
        batch.drop_column("unit")
