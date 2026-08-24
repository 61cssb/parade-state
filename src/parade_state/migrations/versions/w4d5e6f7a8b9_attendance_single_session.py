"""attendance single session

Revision ID: w4d5e6f7a8b9
Revises: v3c4d5e6f7a8
Create Date: 2026-08-24 00:00:00.000000

Collapses attendance to a single daily session — issue 33. The 9-value
status vocabulary carried per AM/PM slot becomes one ``status``
(present/absent) plus a nullable ``reason`` (mc / off / early_outpro /
other / awol) that classifies remarks and never feeds reporting, plus a
single ``remarks``.

Per-slot mapping:

- ``present``           → present, reason null
- ``absent``            → absent,  reason null
- ``mc``                → absent,  reason mc
- ``time_off``          → absent,  reason off
- ``outpro``            → absent,  reason early_outpro (end-of-stint too)
- ``late``              → present, reason null, ``"Late"`` appended to remarks
- ``yet_to_inpro``      → absent,  reason null (Inpro column carries this)
- ``reporting_sick``    → absent,  reason other
- ``att_out``           → absent,  reason other
- any stray legacy value→ absent,  reason null

AM/PM collapse: the PM slot's mapped (status, reason) wins when the PM
slot carried a non-default status (old ``status_pm != 'absent'``);
otherwise the AM mapping wins — so a row marked only in the AM (e.g.
AM=mc, PM=absent-default) keeps absent + reason mc. Remarks from both
slots are joined with ``"; "`` (empties skipped, AM first), with
``"Late"`` appended after a slot's own remarks for old ``late`` slots.

Because status values are *removed*, PostgreSQL needs a native-enum
rebuild of ``attendance_status`` (drop both slot columns → drop type →
recreate with 2 values) rather than in-place ADD VALUE; the new
``attendance_reason`` type is created alongside. SQLite rebuilds via
batch_alter_table. The mapping runs Python-side (readable, and the
collapse rules are too involved for SQL); rows are keyed by the ``id``
PK. Per-value remap counts are logged for both slots (precedent
g7b8c9d0e1f2).

The downgrade is intentionally lossy on data: ``status`` is written back
into BOTH slots; reason → legacy status only where the mapping is 1:1
(``other`` collapses to ``reporting_sick`` — ``att_out`` is
indistinguishable; ``awol`` has no legacy counterpart and degrades to
plain ``absent``); the joined remarks are copied to both slots and the
``"Late"`` marker is not re-derived. The schema shape (columns + enum
values) is fully restored. Both directions are shape-guarded (precedent
v3c4d5e6f7a8): a database already in the target shape but version-
stamped older is detected via column inspection and skipped as a no-op.
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "v3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

NEW_STATUSES = ("present", "absent")
REASONS = ("mc", "off", "early_outpro", "other", "awol")
LEGACY_STATUSES = (
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

# Old slot status -> (new status, new reason).
_SLOT_MAP: dict[str, tuple[str, str | None]] = {
    "present": ("present", None),
    "absent": ("absent", None),
    "mc": ("absent", "mc"),
    "time_off": ("absent", "off"),
    "outpro": ("absent", "early_outpro"),
    "late": ("present", None),
    "yet_to_inpro": ("absent", None),
    "reporting_sick": ("absent", "other"),
    "att_out": ("absent", "other"),
}

# (status, reason) -> legacy status for the lossy downgrade. ``other``
# collapses to reporting_sick (att_out is indistinguishable) and awol
# degrades to plain absent — no legacy counterpart exists.
_REVERSE_MAP: dict[tuple[str, str | None], str] = {
    ("present", None): "present",
    ("absent", None): "absent",
    ("absent", "mc"): "mc",
    ("absent", "off"): "time_off",
    ("absent", "early_outpro"): "outpro",
    ("absent", "other"): "reporting_sick",
    ("absent", "awol"): "absent",
}


def _attendance_columns(bind) -> set[str]:
    """Names of the attendance table's current columns (dialect-agnostic)."""
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("attendance")}


def _log_remap_counts(bind) -> None:
    """Warn with per-value row counts for both slots before the remap."""
    for column in ("status_am", "status_pm"):
        counts = bind.execute(
            sa.text(
                f"SELECT {column}, COUNT(*) FROM attendance GROUP BY {column}"
            )
        ).all()
        for value, count in counts:
            new_status, new_reason = _SLOT_MAP.get(value, ("absent", None))
            logger.warning(
                "Migrated %d attendance.%s values %r -> status=%r reason=%r",
                count,
                column,
                value,
                new_status,
                new_reason,
            )


def _collapse(
    status_am: str | None,
    remarks_am: str | None,
    status_pm: str | None,
    remarks_pm: str | None,
) -> tuple[str, str | None, str | None]:
    """Map one legacy AM/PM row onto (status, reason, remarks)."""
    am_status, am_reason = _SLOT_MAP.get(status_am or "absent", ("absent", None))
    pm_status, pm_reason = _SLOT_MAP.get(status_pm or "absent", ("absent", None))

    # PM's mapping wins when the PM slot carried a non-default status;
    # otherwise AM's does (an untouched PM default must not erase AM).
    if status_pm and status_pm != "absent":
        status, reason = pm_status, pm_reason
    else:
        status, reason = am_status, am_reason

    parts: list[str] = []
    for slot_status, slot_remarks in (
        (status_am, remarks_am),
        (status_pm, remarks_pm),
    ):
        if slot_remarks:
            parts.append(slot_remarks)
        if slot_status == "late":
            parts.append("Late")
    remarks = "; ".join(parts) if parts else None
    return status, reason, remarks


def _legacy_status(status: str | None, reason: str | None) -> str:
    """Best-effort legacy slot status for the lossy downgrade."""
    if not status:
        return "absent"
    return _REVERSE_MAP.get((status, reason), "present" if status == "present" else "absent")


def upgrade() -> None:
    """Collapse AM/PM slots into single status/reason/remarks columns."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # Shape guard: a database already carrying the single-session shape
    # but version-stamped older has nothing to migrate.
    columns = _attendance_columns(bind)
    if "status" in columns and "status_am" not in columns:
        logger.warning(
            "attendance already has single-session status (no status_am) — "
            "skipping the collapse migration as a no-op"
        )
        return

    # Step 1: capture per-value counts for the log (before any changes).
    _log_remap_counts(bind)

    # Step 2: read every legacy row and compute the mapped values
    # (Python-side; rows keyed by the id PK).
    rows = bind.execute(
        sa.text(
            "SELECT id, status_am, remarks_am, status_pm, remarks_pm "
            "FROM attendance"
        )
    ).mappings().all()
    mapped: list[dict] = []
    for row in rows:
        status, reason, remarks = _collapse(
            row["status_am"],
            row["remarks_am"],
            row["status_pm"],
            row["remarks_pm"],
        )
        mapped.append({"id": row["id"], "s": status, "r": reason, "m": remarks})

    # Step 3: schema swap.
    if is_pg:
        # Native-enum rebuild: values are removed, so ADD VALUE cannot be
        # used. Drop both slot columns, rebuild attendance_status with 2
        # values, then add the new columns (reason gets its own type).
        op.drop_column("attendance", "status_am")
        op.drop_column("attendance", "status_pm")
        op.execute("DROP TYPE attendance_status")
        op.execute(
            "CREATE TYPE attendance_status AS ENUM ('present', 'absent')"
        )
        op.execute(
            "CREATE TYPE attendance_reason AS ENUM "
            "('mc', 'off', 'early_outpro', 'other', 'awol')"
        )
        op.execute(
            "ALTER TABLE attendance ADD COLUMN status "
            "attendance_status NOT NULL DEFAULT 'absent'"
        )
        op.execute("ALTER TABLE attendance ADD COLUMN reason attendance_reason")
        op.execute("ALTER TABLE attendance ADD COLUMN remarks TEXT")
    else:
        # SQLite stores sa.Enum as VARCHAR + CHECK; batch_alter_table
        # rebuilds the table. render_as_batch=True is set in env.py.
        with op.batch_alter_table("attendance", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "status",
                    sa.Enum(*NEW_STATUSES, name="attendance_status"),
                    nullable=False,
                    server_default="absent",
                )
            )
            batch_op.add_column(
                sa.Column(
                    "reason",
                    sa.Enum(*REASONS, name="attendance_reason"),
                    nullable=True,
                )
            )
            batch_op.add_column(sa.Column("remarks", sa.Text(), nullable=True))

    # Step 4: write the mapped values back (NOT NULL default filled
    # 'absent' above; this sets the real collapsed values).
    if mapped:
        bind.execute(
            sa.text(
                "UPDATE attendance SET status = :s, reason = :r, remarks = :m "
                "WHERE id = :id"
            ),
            mapped,
        )

    # Step 5: drop the legacy slot columns (data already merged). The PG
    # branch dropped status_am/status_pm before rebuilding the enum type.
    if is_pg:
        op.drop_column("attendance", "remarks_am")
        op.drop_column("attendance", "remarks_pm")
    else:
        with op.batch_alter_table("attendance", schema=None) as batch_op:
            batch_op.drop_column("status_am")
            batch_op.drop_column("status_pm")
            batch_op.drop_column("remarks_am")
            batch_op.drop_column("remarks_pm")


def downgrade() -> None:
    """Restore the AM/PM shape (data is lossy — see docstring)."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # Shape guard (mirror of the upgrade guard): nothing to restore.
    columns = _attendance_columns(bind)
    if "status_am" in columns and "status" not in columns:
        logger.warning(
            "attendance already has AM/PM slots (no single status) — "
            "skipping the downgrade as a no-op"
        )
        return

    # Read the collapsed values before dropping their columns.
    rows = bind.execute(
        sa.text("SELECT id, status, reason, remarks FROM attendance")
    ).mappings().all()
    mapped: list[dict] = []
    for row in rows:
        mapped.append(
            {
                "id": row["id"],
                "s": _legacy_status(row["status"], row["reason"]),
                "m": row["remarks"],
            }
        )

    if is_pg:
        op.drop_column("attendance", "status")
        op.drop_column("attendance", "reason")
        op.drop_column("attendance", "remarks")
        op.execute("DROP TYPE attendance_status")
        op.execute("DROP TYPE attendance_reason")
        legacy_values = ", ".join(f"'{v}'" for v in LEGACY_STATUSES)
        op.execute(
            f"CREATE TYPE attendance_status AS ENUM ({legacy_values})"
        )
        op.execute(
            "ALTER TABLE attendance ADD COLUMN status_am "
            "attendance_status NOT NULL DEFAULT 'absent'"
        )
        op.execute(
            "ALTER TABLE attendance ADD COLUMN status_pm "
            "attendance_status NOT NULL DEFAULT 'absent'"
        )
        op.execute("ALTER TABLE attendance ADD COLUMN remarks_am TEXT")
        op.execute("ALTER TABLE attendance ADD COLUMN remarks_pm TEXT")
    else:
        with op.batch_alter_table("attendance", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "status_am",
                    sa.Enum(*LEGACY_STATUSES, name="attendance_status"),
                    nullable=False,
                    server_default="absent",
                )
            )
            batch_op.add_column(
                sa.Column(
                    "status_pm",
                    sa.Enum(*LEGACY_STATUSES, name="attendance_status"),
                    nullable=False,
                    server_default="absent",
                )
            )
            batch_op.add_column(sa.Column("remarks_am", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("remarks_pm", sa.Text(), nullable=True))

    # Write the collapsed values back into both slots (lossy).
    if mapped:
        bind.execute(
            sa.text(
                "UPDATE attendance SET status_am = :s, status_pm = :s, "
                "remarks_am = :m, remarks_pm = :m WHERE id = :id"
            ),
            mapped,
        )

    # SQLite still carries the single-session columns (the PG branch
    # dropped them before rebuilding the enum type) — remove them now.
    if not is_pg:
        with op.batch_alter_table("attendance", schema=None) as batch_op:
            batch_op.drop_column("status")
            batch_op.drop_column("reason")
            batch_op.drop_column("remarks")
