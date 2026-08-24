"""Migration mapping tests for issue 32 (callup_status → inpro_status).

Runs the real alembic chain against a throwaway SQLite database: upgrade to
the pre-change revision, seed personnel rows across the legacy callup
vocabulary, upgrade to head, then assert the value/remark mapping and the
schema swap (column, enum CHECK, index). The downgrade path is exercised
best-effort as documented in the migration (inproed collapses to Called Up;
appended "Previously:" remarks are not removed).
"""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from parade_state.db.restore import _migrations_dir
from parade_state.utils import env

# The revision immediately before the inpro rename (issue 32).
PRE_INPRO_REVISION = "u2b3c4d5e6f7"


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


def _seed_legacy_personnel(db_path) -> None:
    """Insert personnel rows spanning the legacy callup vocabulary.

    FKs are not enforced on SQLite by default, so the nominal-roll/user
    parent rows are not needed for the mapping assertions.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO personnel "
            "(id, nominal_roll_id, rank, category, full_name, unit, status, "
            " callup_status, remarks, extra_fields, created_at, created_by) "
            "VALUES (?, ?, 'PTE', 'WOSE', ?, 'Coy A', 'active', ?, ?, '{}', "
            "        '2026-08-01 00:00:00', 'seed-user')",
            [
                ("p-mig-1", "nr-1", "Called Up Person", "Called Up", None),
                ("p-mig-2", "nr-1", "Deferred Person", "Deferred", None),
                ("p-mig-3", "nr-1", "MR Person", "MR", None),
                ("p-mig-4", "nr-1", "Disrupted Person", "Disrupted", None),
                # Pre-existing remarks must be preserved, note appended.
                ("p-mig-5", "nr-1", "Age Limit Person", "Age Limit", "course till Friday"),
                ("p-mig-6", "nr-1", "Other Person", "Other", None),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_personnel(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return {
            row["full_name"]: row
            for row in conn.execute(
                "SELECT full_name, inpro_status, remarks FROM personnel"
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


def _index_names(db_path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            row[0]
            for row in conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='index' "
                f"AND tbl_name='{table}'"
            )
        }
    finally:
        conn.close()


def test_migration_maps_callup_to_inpro(tmp_path):
    db_path = tmp_path / "mig.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    _run_upgrade(url, PRE_INPRO_REVISION)
    _seed_legacy_personnel(db_path)
    _run_upgrade(url, "head")

    rows = _fetch_personnel(db_path)

    # Direct mappings.
    assert rows["Called Up Person"]["inpro_status"] == "yet_to_inpro"
    assert rows["Called Up Person"]["remarks"] is None
    assert rows["Deferred Person"]["inpro_status"] == "deferred"
    assert rows["Deferred Person"]["remarks"] is None

    # Legacy decision statuses collapse to yet_to_inpro with the decision
    # appended to remarks (issue 32 mapping table).
    assert rows["MR Person"]["inpro_status"] == "yet_to_inpro"
    assert rows["MR Person"]["remarks"] == "Previously: MR"
    assert rows["Disrupted Person"]["inpro_status"] == "yet_to_inpro"
    assert rows["Disrupted Person"]["remarks"] == "Previously: Disrupted"

    # Pre-existing remarks are preserved with the note appended.
    assert rows["Age Limit Person"]["inpro_status"] == "yet_to_inpro"
    assert rows["Age Limit Person"]["remarks"] == (
        "course till Friday; Previously: Age Limit"
    )
    assert rows["Other Person"]["inpro_status"] == "yet_to_inpro"
    assert rows["Other Person"]["remarks"] == "Previously: Other"

    # Schema swap: old column/index gone, new ones present.
    assert "callup_status" not in _columns(db_path, "personnel")
    assert "inpro_status" in _columns(db_path, "personnel")
    indexes = _index_names(db_path, "personnel")
    assert "ix_personnel_inpro_status" in indexes
    assert "ix_personnel_callup_status" not in indexes


@pytest.mark.parametrize(
    "inpro_value,expected_callup",
    [
        ("deferred", "Deferred"),   # the only recoverable distinction
        ("inproed", "Called Up"),   # collapses (lossy, documented)
        ("yet_to_inpro", "Called Up"),
    ],
)
def test_downgrade_restores_callup_shape(tmp_path, inpro_value, expected_callup):
    db_path = tmp_path / "mig-down.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    _run_upgrade(url, "head")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO personnel "
            "(id, nominal_roll_id, rank, category, full_name, unit, status, "
            " inpro_status, extra_fields, created_at, created_by) "
            "VALUES ('p-dg-1', 'nr-1', 'PTE', 'WOSE', 'Down Person', 'Coy A', "
            "        'active', ?, '{}', '2026-08-01 00:00:00', 'seed-user')",
            (inpro_value,),
        )
        conn.commit()
    finally:
        conn.close()

    _run_downgrade(url, PRE_INPRO_REVISION)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT callup_status FROM personnel WHERE id = 'p-dg-1'"
        ).fetchone()
    finally:
        conn.close()
    assert row["callup_status"] == expected_callup

    assert "inpro_status" not in _columns(db_path, "personnel")
    assert "callup_status" in _columns(db_path, "personnel")
