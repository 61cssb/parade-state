"""add_original_filename_to_csv_uploads

Revision ID: a1b2c3d4e5f6
Revises: bef66a2a675e
Create Date: 2026-06-24 00:00:00.000000

Stores the upload-time filename on CsvUpload so each Estab (via its linked
CsvUpload) carries a visible source-file association. Nullable to preserve
existing rows. The file-reference naming convention is a pending decision
(see docs/NEXT_PHASE.md).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "bef66a2a675e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "csv_uploads",
        sa.Column("original_filename", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("csv_uploads", "original_filename")
