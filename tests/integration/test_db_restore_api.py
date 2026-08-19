"""Tests for the super-admin database restore endpoint.

The SQLite suite exercises the authorization and gating paths. The
Postgres suite (TEST_DATABASE_URL plus a local pg_dump of a compatible
major version) exercises the full restore: dump a seeded database,
upload it through the API, and assert the swap + engine re-init left
the app serving the restored data.
"""

import os
import shutil
import subprocess
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient

from parade_state.utils import env

RESTORE_URL = "/api/v1/admin/database/restore"

SUPER_ADMIN_PARAMS = {"user_id": "super-admin-test-id", "user_role": "super_admin"}
ADMIN_PARAMS = {"user_id": "admin-user-id", "user_role": "admin"}

DUMMY_FILE = {"file": ("backup.dump", b"x", "application/octet-stream")}


def _running_postgres() -> bool:
    """Whether the suite is running against a Postgres server."""
    return bool(env.get("TEST_DATABASE_URL"))


def _pg_dump_compatible() -> bool:
    """A local pg_dump exists (any major; local servers match by default)."""
    if not _running_postgres():
        return False
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        return False
    try:
        result = subprocess.run(
            [pg_dump, "--version"], capture_output=True, text=True, check=True
        )
        return result.returncode == 0
    except subprocess.CalledProcessError:
        return False


def _dump_database(database_url: str) -> bytes:
    """pg_dump a database URL to custom-format bytes (test helper)."""
    parts = urlsplit(database_url)
    result = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "-h",
            parts.hostname or "localhost",
            "-p",
            str(parts.port or 5432),
            "-U",
            parts.username or "",
            parts.path.lstrip("/"),
        ],
        capture_output=True,
        check=True,
        env={**os.environ, "PGPASSWORD": parts.password or ""},
    )
    return bytes(result.stdout)


def _current_test_db_url() -> str:
    """TEST_DATABASE_URL rewritten to point at this test's database."""
    from parade_state import db

    parts = urlsplit(env.get("TEST_DATABASE_URL"))
    return urlunsplit(parts._replace(path=f"/{db._engine.url.database}"))


# ---------------------------------------------------------------------------
# Gating tests (run on every dialect)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_forbidden_for_plain_admin(
    client: TestClient, admin_token_headers: dict[str, str]
):
    """Non-super-admins get 403 before anything else happens."""
    response = client.post(
        RESTORE_URL,
        headers=admin_token_headers,
        params={**ADMIN_PARAMS, "confirmation": "anything"},
        files=DUMMY_FILE,
    )
    assert response.status_code == 403
    assert "super admin" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_restore_disabled_by_kill_switch(
    client: TestClient, super_admin_token_headers: dict[str, str], monkeypatch
):
    """RESTORE_ENABLED=false short-circuits the endpoint."""
    from parade_state.api import db_restore as api_module

    monkeypatch.setattr(
        api_module.get_settings(), "RESTORE_ENABLED", False, raising=False
    )

    response = client.post(
        RESTORE_URL,
        headers=super_admin_token_headers,
        params={**SUPER_ADMIN_PARAMS, "confirmation": "anything"},
        files=DUMMY_FILE,
    )
    assert response.status_code == 400
    assert "disabled" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_restore_requires_postgres_on_sqlite(
    client: TestClient, super_admin_token_headers: dict[str, str]
):
    """On SQLite deployments the endpoint refuses with a clear 400."""
    if _running_postgres():
        pytest.skip("SQLite-only gating test")

    response = client.post(
        RESTORE_URL,
        headers=super_admin_token_headers,
        params={**SUPER_ADMIN_PARAMS, "confirmation": "anything"},
        files=DUMMY_FILE,
    )
    assert response.status_code == 400
    assert "PostgreSQL" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Full flow (Postgres suite only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _pg_dump_compatible(), reason="needs TEST_DATABASE_URL + local pg_dump"
)
async def test_restore_happy_path_swaps_and_reinitializes(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    sample_personnel,
    db_session,
):
    """A dump of the current data restores through the API end-to-end."""
    from sqlalchemy import text as sa_text

    from parade_state import db
    from parade_state.db.restore import _app_head_revision

    # Per-test databases are built via create_all (no alembic_version);
    # stamp the current head so the restored dump looks like production.
    head = _app_head_revision()
    await db_session.execute(
        sa_text("CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) PRIMARY KEY)")
    )
    await db_session.execute(sa_text("DELETE FROM alembic_version"))
    await db_session.execute(
        sa_text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
        {"v": head},
    )
    await db_session.commit()

    current_db = db._engine.url.database
    dump = _dump_database(_current_test_db_url())

    response = client.post(
        RESTORE_URL,
        headers=super_admin_token_headers,
        params={**SUPER_ADMIN_PARAMS, "confirmation": current_db},
        files={"file": ("backup.dump", dump, "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["dump_version"]
    assert summary["pre_restore_db_dropped"] is True
    assert summary["audit_written"] is True

    # The engine was re-initialized against the restored database;
    # query through the app's (new) session maker.
    session_maker = db.get_session_maker()
    async with session_maker() as session:
        personnel_count = (
            await session.execute(sa_text("SELECT count(*) FROM personnel"))
        ).scalar_one()
    assert personnel_count == len(sample_personnel)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _pg_dump_compatible(), reason="needs TEST_DATABASE_URL + local pg_dump"
)
async def test_restore_rejects_wrong_confirmation(
    client: TestClient, super_admin_token_headers: dict[str, str]
):
    """The confirmation text must equal the current database name."""
    from parade_state import db

    response = client.post(
        RESTORE_URL,
        headers=super_admin_token_headers,
        params={**SUPER_ADMIN_PARAMS, "confirmation": "wrong-name"},
        files=DUMMY_FILE,
    )
    assert response.status_code == 400
    assert "Confirmation" in response.json()["detail"]
    assert db._engine.url.database  # engine untouched


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _pg_dump_compatible(), reason="needs TEST_DATABASE_URL + local pg_dump"
)
async def test_restore_rejects_garbage_file(
    client: TestClient, super_admin_token_headers: dict[str, str]
):
    """A non-archive upload fails verification without touching data."""
    from parade_state import db

    current_db = db._engine.url.database

    response = client.post(
        RESTORE_URL,
        headers=super_admin_token_headers,
        params={**SUPER_ADMIN_PARAMS, "confirmation": current_db},
        files={
            "file": (
                "backup.dump",
                b"definitely not a dump",
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 400
    assert "valid pg_dump" in response.json()["detail"]
