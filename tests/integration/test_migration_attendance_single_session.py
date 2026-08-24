"""Migration mapping tests for issue 33 (AM/PM slots → single session).

Runs the real alembic chain against a throwaway SQLite database: upgrade to
the pre-change revision, seed attendance rows across the legacy 9-value
vocabulary in both slots, upgrade to head, then assert the status/reason
collapse (PM wins when its slot was marked; remarks joined with "; ";
"Late" appended) and the schema swap. The downgrade path is exercised
best-effort as documented in the migration (status copied into both slots;
reason → legacy status only where 1:1).
"""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from parade_state.db.restore import _migrations_dir
from parade_state.utils import env

# The revision immediately before the single-session collapse (issue 33).
PRE_SINGLE_SESSION_REVISION = "v3c4d5e6f7a8"


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(_migrations_dir()))
    return config


def _run_upgrade(database_url: str, revision: str) -> None:
    """Programmatic alembic upgrade (mirrors db.restore._run_upgrade)."""
    config = _alembic_config()
    with env.override("DATABASE_URL", database_url):
        command.upgrade(config, revision)


def _run_downgrade(database_url: str, revision: str) -> None:
    config = _alembic_config()
    with env.override("DATABASE_URL", database_url):
        command.downgrade(config, revision)


def _insert_legacy_attendance(db_path, rows) -> None:
    """Insert legacy AM/PM attendance rows.

    FKs are not enforced on SQLite by default, so the personnel/NR parent
    rows are not needed for the mapping assertions. Each row is
    (id, status_am, remarks_am, status_pm, remarks_pm); the row id doubles
    as the personnel_id to satisfy the (personnel_id, date) unique
    constraint.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO attendance "
            "(id, personnel_id, nominal_roll_id, date, "
            " status_am, remarks_am, status_pm, remarks_pm, "
            " created_at, created_by, updated_at, updated_by, "
            " is_retroactive_edit) "
            "VALUES (?, ?, 'nr-1', '2026-08-20', "
            "        ?, ?, ?, ?, "
            "        '2026-08-20 08:00:00', 'seed-user', "
            "        '2026-08-20 08:00:00', 'seed-user', 0)",
            [(r[0], r[0], *r[1:]) for r in rows],
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_attendance(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return {
            row["id"]: row
            for row in conn.execute(
                "SELECT id, status, reason, remarks FROM attendance"
            )
        }
    finally:
        conn.close()


def _columns(db_path, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        ]
    finally:
        conn.close()


def test_migration_collapses_am_pm_to_single_session(tmp_path):
    db_path = tmp_path / "mig.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    _run_upgrade(url, PRE_SINGLE_SESSION_REVISION)
    _insert_legacy_attendance(
        db_path,
        [
            # Direct mappings on a single marked slot.
            ("a-present", "present", None, "absent", None),
            ("a-absent", "absent", None, "absent", None),
            ("a-mc", "mc", None, "absent", None),
            ("a-time-off", "time_off", None, "absent", None),
            ("a-outpro", "outpro", None, "absent", None),
            ("a-yet-to-inpro", "yet_to_inpro", None, "absent", None),
            ("a-reporting-sick", "reporting_sick", None, "absent", None),
            ("a-att-out", "att_out", None, "absent", None),
            # AM-only marked keeps its mapping (untouched PM default).
            # Late → present, "Late" appended after the slot's remarks.
            ("a-late", "late", "Official duty", "absent", None),
            # PM wins when both slots are marked.
            ("a-pm-wins", "present", None, "mc", None),
            ("a-pm-late", "present", None, "late", None),
            # PM-only marked.
            ("a-pm-only", "absent", None, "mc", None),
            # Remarks join with "; " (AM first), empties skipped.
            ("a-join", "present", "AM note", "absent", "PM note"),
        ],
    )
    _run_upgrade(url, "head")

    rows = _fetch_attendance(db_path)

    # Per-slot mapping table (issue 33).
    assert (rows["a-present"]["status"], rows["a-present"]["reason"]) == ("present", None)
    assert (rows["a-absent"]["status"], rows["a-absent"]["reason"]) == ("absent", None)
    assert (rows["a-mc"]["status"], rows["a-mc"]["reason"]) == ("absent", "mc")
    assert (rows["a-time-off"]["status"], rows["a-time-off"]["reason"]) == ("absent", "off")
    assert (rows["a-outpro"]["status"], rows["a-outpro"]["reason"]) == ("absent", "early_outpro")
    assert (rows["a-yet-to-inpro"]["status"], rows["a-yet-to-inpro"]["reason"]) == ("absent", None)
    assert (rows["a-reporting-sick"]["status"], rows["a-reporting-sick"]["reason"]) == ("absent", "other")
    assert (rows["a-att-out"]["status"], rows["a-att-out"]["reason"]) == ("absent", "other")

    # Late maps to present with "Late" appended after the slot's remarks.
    assert (rows["a-late"]["status"], rows["a-late"]["reason"]) == ("present", None)
    assert rows["a-late"]["remarks"] == "Official duty; Late"

    # PM's mapping wins when the PM slot carried a non-default status.
    assert (rows["a-pm-wins"]["status"], rows["a-pm-wins"]["reason"]) == ("absent", "mc")
    assert (rows["a-pm-late"]["status"], rows["a-pm-late"]["reason"]) == ("present", None)
    assert rows["a-pm-late"]["remarks"] == "Late"
    # PM-only marked row keeps PM's mapping.
    assert (rows["a-pm-only"]["status"], rows["a-pm-only"]["reason"]) == ("absent", "mc")

    # Remarks from both slots join with "; " (AM first).
    assert rows["a-join"]["remarks"] == "AM note; PM note"

    # Schema swap: legacy slot columns gone, single-session columns present.
    columns = _columns(db_path, "attendance")
    for legacy in ("status_am", "status_pm", "remarks_am", "remarks_pm"):
        assert legacy not in columns
    for new in ("status", "reason", "remarks"):
        assert new in columns


@pytest.mark.parametrize(
    "status,reason,expected_slot",
    [
        ("present", None, "present"),
        ("absent", None, "absent"),
        ("absent", "mc", "mc"),
        ("absent", "off", "time_off"),
        ("absent", "early_outpro", "outpro"),
        ("present", "other", "present"),  # reason ignored on present rows
        ("absent", "other", "reporting_sick"),  # att_out indistinguishable (lossy)
        ("absent", "awol", "absent"),  # no legacy counterpart (lossy)
    ],
)
def test_downgrade_restores_am_pm_shape(tmp_path, status, reason, expected_slot):
    db_path = tmp_path / "mig-down.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    _run_upgrade(url, "head")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO attendance "
            "(id, personnel_id, nominal_roll_id, date, "
            " status, reason, remarks, "
            " created_at, created_by, updated_at, updated_by, "
            " is_retroactive_edit) "
            "VALUES ('a-dg-1', 'p-dg-1', 'nr-1', '2026-08-20', "
            "        ?, ?, 'kept remark', "
            "        '2026-08-20 08:00:00', 'seed-user', "
            "        '2026-08-20 08:00:00', 'seed-user', 0)",
            (status, reason),
        )
        conn.commit()
    finally:
        conn.close()

    _run_downgrade(url, PRE_SINGLE_SESSION_REVISION)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status_am, status_pm, remarks_am, remarks_pm "
            "FROM attendance WHERE id = 'a-dg-1'"
        ).fetchone()
    finally:
        conn.close()

    # The collapsed value lands in both slots; remarks copied to both.
    assert row["status_am"] == expected_slot
    assert row["status_pm"] == expected_slot
    assert row["remarks_am"] == "kept remark"
    assert row["remarks_pm"] == "kept remark"

    columns = _columns(db_path, "attendance")
    for legacy in ("status_am", "status_pm", "remarks_am", "remarks_pm"):
        assert legacy in columns
    for new in ("status", "reason", "remarks"):
        assert new not in columns
