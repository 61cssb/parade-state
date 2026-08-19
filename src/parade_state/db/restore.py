"""Safe in-app restore of the production database from a dump file.

The restore never touches the live database until the replacement is
fully built and verified:

1. validate the uploaded archive (``pg_restore --list``; version guard)
2. restore into a freshly created temporary database on the same server
3. verify: known alembic revision not newer than the code's head, core
   tables present, collect row counts
4. swap: dispose the app engine, terminate backends, rename the current
   database away, rename the temporary one into place, re-initialize
5. if the dump was older than head, run ``alembic upgrade head``
6. audit-log the operation, drop the displaced database

Every failure path drops or renames so the app always ends up connected
to a valid database.
"""

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from parade_state import db
from parade_state.models import AuditLog
from parade_state.utils import env, ids, utc_dt

# Tables that must exist in a restored database for it to count as ours.
CORE_TABLES = ("users", "personnel", "attendance", "nominal_rolls")

# Row counts reported in the verification summary.
COUNTED_TABLES = ("users", "nominal_rolls", "personnel", "attendance", "audit_logs")


class RestoreError(Exception):
    """Restore failed before the live database was modified.

    ``status_code`` maps to the HTTP response; anything raised after the
    swap keeps a 500 but carries the fallback database name in its
    message.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _migrations_dir() -> Path:
    """Directory of the alembic scripts (ships inside the package)."""
    import parade_state

    return Path(parade_state.__file__).resolve().parent / "migrations"


def _app_head_revision() -> str:
    """Head alembic revision of the deployed code."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(_migrations_dir()))
    head = script.get_current_head()
    assert head is not None
    return head


def _revision_depth(revision: str) -> int | None:
    """Position of ``revision`` in the chain (0 = base), None if unknown.

    Follows ``down_revision`` links from the target so it does not
    depend on walking APIs that changed across alembic versions.
    """
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(_migrations_dir()))
    try:
        current = script.get_revision(revision)
    except Exception:
        return None
    if current is None:
        return None

    depth = 0
    while isinstance(current.down_revision, str):
        current = script.get_revision(current.down_revision)
        depth += 1
    return depth


class _ConnectionInfo:
    """Connection facts taken from the live engine's URL.

    Derived from the engine (not Settings) so test suites and any
    runtime reconfiguration stay in sync with what the app is actually
    connected to.
    """

    def __init__(self, url) -> None:
        query = dict(url.query)

        self.host = url.host or "localhost"
        self.port = url.port or 5432
        self.user = url.username or ""
        self.password = url.password or ""
        self.database = url.database or "postgres"
        self.sslmode = query.get("sslmode") or query.get("ssl") or "prefer"

        if not self.user or not self.password:
            raise RestoreError(
                "The database URL must include credentials for a restore",
                status_code=500,
            )

    def async_url(self, database: str) -> str:
        """SQLAlchemy async URL for a database on this server."""
        auth = f"{self.user}:{self.password}"
        return (
            f"postgresql+asyncpg://{auth}@{self.host}:{self.port}/{database}"
        )

    def subprocess_env(self) -> dict[str, str]:
        """Environment for pg_restore/pg_dump subprocesses (no argv secrets)."""
        child_env = env.environ()
        child_env["PGPASSWORD"] = self.password
        child_env["PGSSLMODE"] = self.sslmode
        return child_env


async def _maintenance_engine(info: _ConnectionInfo):
    """Engine to the server's maintenance database (autocommit DDL)."""
    return create_async_engine(
        info.async_url("postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )


async def _run_subprocess(
    args: list[str], *, stdin_bytes: bytes | None = None, env_extra: dict | None = None
) -> tuple[int, bytes, bytes]:
    """Run a subprocess, returning (returncode, stdout, stderr)."""
    child_env = env.environ()
    if env_extra:
        child_env.update(env_extra)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env,
    )
    stdout, stderr = await proc.communicate(stdin_bytes)
    return proc.returncode or 0, stdout, stderr


def _major(version_string: str) -> int:
    """Major version number from a Postgres tool version string."""
    match = re.search(r"(\d+)", version_string)
    if not match:
        raise RestoreError(
            f"Could not parse Postgres version from {version_string!r}",
            status_code=500,
        )
    return int(match.group(1))


async def _parse_dump_header(dump: bytes) -> dict:
    """Validate the archive and extract header facts via pg_restore --list."""
    if shutil.which("pg_restore") is None:
        raise RestoreError(
            "pg_restore is not available on the server", status_code=500
        )

    code, stdout, _ = await _run_subprocess(
        ["pg_restore", "--list"], stdin_bytes=dump
    )
    if code != 0:
        raise RestoreError(
            "File is not a valid pg_dump custom-format archive", status_code=400
        )

    listing = stdout.decode("utf-8", errors="replace")

    dumped_from = re.search(r"Dumped from database version: (\d+)", listing)
    if not dumped_from:
        raise RestoreError("Dump header is missing its version", status_code=400)

    code, stdout, _ = await _run_subprocess(["pg_restore", "--version"])  # noqa: F841
    client_major = _major(stdout.decode())
    dump_major = int(dumped_from.group(1))

    if dump_major > client_major:
        raise RestoreError(
            f"Dump is from PostgreSQL {dump_major} but the server has "
            f"pg_restore {client_major}; a newer client is required",
            status_code=400,
        )

    has_alembic = "alembic_version" in listing
    return {
        "dump_major": dump_major,
        "client_major": client_major,
        "has_alembic_table": has_alembic,
    }


async def _create_database(admin_engine, name: str) -> None:
    async with admin_engine.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))


async def _drop_database(admin_engine, name: str) -> None:
    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


async def _restore_into(info: _ConnectionInfo, name: str, dump: bytes) -> None:
    """pg_restore the archive into database ``name`` (dump on stdin)."""
    code, _, stderr = await _run_subprocess(
        [
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            "-h",
            info.host,
            "-p",
            str(info.port),
            "-U",
            info.user,
            "-d",
            name,
        ],
        stdin_bytes=dump,
        env_extra={
            "PGPASSWORD": info.password,
            "PGSSLMODE": info.sslmode,
        },
    )
    if code != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise RestoreError(f"pg_restore failed: {detail}", status_code=500)


async def _verify(info: _ConnectionInfo, name: str) -> dict:
    """Verify the restored database and collect the summary facts."""
    engine = create_async_engine(info.async_url(name), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tables = {
                row[0]
                for row in await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                )
            }

            missing = [t for t in CORE_TABLES if t not in tables]
            if missing:
                raise RestoreError(
                    "Restored database is missing core tables: "
                    + ", ".join(missing),
                    status_code=400,
                )

            version_row = await conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            dump_version = version_row.scalar_one_or_none()
            if not dump_version:
                raise RestoreError(
                    "Restored database has no alembic_version row",
                    status_code=400,
                )

            head = _app_head_revision()
            depth = _revision_depth(dump_version)
            if depth is None:
                raise RestoreError(
                    f"Dump's migration revision {dump_version} is not known "
                    "to this build; restore would need newer code",
                    status_code=400,
                )
            head_depth = _revision_depth(head)
            assert head_depth is not None
            if depth > head_depth:
                raise RestoreError(
                    f"Dump's schema ({dump_version}) is newer than this "
                    f"build's head ({head}); refusing to restore",
                    status_code=400,
                )

            row_counts: dict[str, int] = {}
            for table in COUNTED_TABLES:
                if table in tables:
                    count = await conn.execute(text(f"SELECT count(*) FROM {table}"))
                    row_counts[table] = count.scalar_one()

            return {
                "dump_version": dump_version,
                "app_head": head,
                "needs_migration": dump_version != head,
                "table_count": len(tables),
                "row_counts": row_counts,
            }
    finally:
        await engine.dispose()


async def _swap_databases(
    admin_engine, info: _ConnectionInfo, timestamp: str
) -> str:
    """Move the current database aside and the restored one into place.

    Returns the displaced database's name (the rollback target).
    """
    pre_restore = f"parade_state_pre_restore_{timestamp}"

    async with admin_engine.connect() as conn:
        # Stop new connections to the live database and evict idle ones
        # so the rename cannot block.
        await conn.execute(
            text(f'ALTER DATABASE "{info.database}" ALLOW_CONNECTIONS false')
        )
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": info.database},
        )

        try:
            await conn.execute(
                text(f'ALTER DATABASE "{info.database}" RENAME TO "{pre_restore}"')
            )
        except Exception:
            await conn.execute(
                text(f'ALTER DATABASE "{info.database}" ALLOW_CONNECTIONS true')
            )
            raise

        try:
            await conn.execute(
                text(f'ALTER DATABASE "parade_state_restore_{timestamp}" '
                     f'RENAME TO "{info.database}"')
            )
        except Exception:
            # Roll the first rename back so the app reconnects to the
            # original database unchanged.
            await conn.execute(
                text(f'ALTER DATABASE "{pre_restore}" RENAME TO "{info.database}"')
            )
            await conn.execute(
                text(f'ALTER DATABASE "{info.database}" ALLOW_CONNECTIONS true')
            )
            raise

        # The displaced database keeps the ALLOW_CONNECTIONS false set
        # before the rename; without this it could not serve as the
        # documented fallback if the post-restore migration fails.
        try:
            await conn.execute(
                text(f'ALTER DATABASE "{pre_restore}" ALLOW_CONNECTIONS true')
            )
        except Exception:  # noqa: BLE001 - swap already succeeded
            logging.getLogger(__name__).warning(
                "could not re-enable connections on fallback database %s",
                pre_restore,
            )

    return pre_restore


def _run_upgrade(database_url: str) -> None:
    """Sync in-process alembic upgrade (runs in a worker thread).

    The migrations env.py reads ``DATABASE_URL`` from the process
    environment, so the variable is set for the duration of the run.
    The config is built programmatically — passing an ini path computed
    from the package location broke on wheel installs (site-packages,
    where no alembic.ini exists), and alembic prints its config errors
    to stdout, which made subprocess failures surface as blank details.
    """
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(_migrations_dir()))
    with env.override("DATABASE_URL", database_url):
        command.upgrade(config, "head")


async def _migrate_restored(database_url: str) -> None:
    """Run alembic upgrade head against the restored database."""
    try:
        await asyncio.to_thread(_run_upgrade, database_url)
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "post-restore alembic upgrade failed"
        )
        raise RestoreError(f"alembic upgrade failed after swap: {exc}", 500)


async def restore_from_dump(dump: bytes, *, operator_id: str) -> dict:
    """Restore the application database from a dump archive.

    Args:
        dump: pg_dump custom-format archive bytes (decrypted by the
            operator; the server never sees age keys).
        operator_id: User ID triggering the restore, for the audit log.

    Returns:
        Verification summary dict (also written to the audit log).

    Raises:
        RestoreError: On any failure; ``status_code`` is suitable for
            the HTTP response. The live database is only modified once
            the replacement has passed verification.
    """
    engine_url = db._engine.url
    # str(url) masks the password (***); render fully for re-init and
    # subprocess hand-off.
    engine_url_string = engine_url.render_as_string(hide_password=False)
    info = _ConnectionInfo(engine_url)
    timestamp = ids.uuid4().hex[:12]
    temp_db = f"parade_state_restore_{timestamp}"

    header = await _parse_dump_header(dump)

    admin_engine = await _maintenance_engine(info)
    try:
        try:
            await _create_database(admin_engine, temp_db)
        except Exception as exc:
            raise RestoreError(f"Could not create restore database: {exc}", 500)

        try:
            await _restore_into(info, temp_db, dump)
        except RestoreError:
            await _drop_database(admin_engine, temp_db)
            raise

        try:
            summary = await _verify(info, temp_db)
        except RestoreError:
            await _drop_database(admin_engine, temp_db)
            raise

        summary["dump_pg_major"] = header["dump_major"]
        summary["restored_at"] = utc_dt.utcnow().isoformat()
        summary["operator_id"] = operator_id

        # --- swap: everything before this point left production intact ---
        await db._engine.dispose()

        try:
            pre_restore = await _swap_databases(admin_engine, info, timestamp)
        except Exception as exc:
            # Engine is disposed; bring it back on the untouched original.
            db.init_database(engine_url_string, poolclass=db._poolclass)
            # Both swap failure modes leave the restored copy under its
            # temp name; drop it so nothing leaks.
            try:
                await _drop_database(admin_engine, temp_db)
            except Exception:  # noqa: BLE001 - cleanup is best-effort
                logging.getLogger(__name__).warning(
                    "could not drop leftover restore database %s", temp_db
                )
            raise RestoreError(f"Database swap failed: {exc}", 500)

        db.init_database(engine_url_string, poolclass=db._poolclass)

        summary["pre_restore_db"] = pre_restore

        if summary["needs_migration"]:
            try:
                await _migrate_restored(engine_url_string)
                summary["migrated_to_head"] = True
            except RestoreError:
                # Data is restored; keep the old database as fallback.
                summary["migrated_to_head"] = False
                summary["audit_written"] = await _write_audit_log(
                    operator_id, info.database, summary
                )
                raise

        await _drop_database(admin_engine, pre_restore)
        summary["pre_restore_db_dropped"] = True

        summary["audit_written"] = await _write_audit_log(
            operator_id, info.database, summary
        )
        return summary
    finally:
        await admin_engine.dispose()


async def _write_audit_log(
    operator_id: str, database: str, summary: dict
) -> bool:
    """Append the restore record using the (re-initialized) app sessions.

    Best-effort: the restore has already succeeded at this point, and a
    restored dump that predates the operator's account would fail the
    audit row's foreign key — never fail the request over the log entry.
    """
    session_maker = db.get_session_maker()
    if session_maker is None:  # pragma: no cover - app always initialized
        return False

    maker: async_sessionmaker = session_maker
    try:
        async with maker() as session:
            session.add(
                AuditLog(
                    user_id=operator_id,
                    entity_type="database",
                    entity_id=database[:36],
                    action="restore",
                    description=json.dumps(summary, default=str),
                )
            )
            await session.commit()
        return True
    except Exception:  # noqa: BLE001 - never fail a successful restore
        logging.getLogger(__name__).exception("restore audit-log write failed")
        return False
