"""Database restore API endpoint (super-admin only)."""

import asyncio

from fastapi import APIRouter, HTTPException, Query, UploadFile, status

from parade_state import db
from parade_state.config import get_settings
from parade_state.db.restore import RestoreError, restore_from_dump

router = APIRouter()

# Dumps are a few MB; 25 MB leaves generous headroom.
MAX_RESTORE_UPLOAD_SIZE = 25 * 1024 * 1024

# Single-flight: one restore at a time per process.
_restore_lock = asyncio.Lock()


@router.post("/database/restore")
async def restore_database(
    file: UploadFile,
    confirmation: str = Query(..., description="Must equal the database name"),
    user_id: str = Query(..., description="User ID triggering the restore"),
    user_role: str = Query(..., description="User role for authorization"),
) -> dict:
    """Restore the application database from a decrypted dump archive.

    Accepts a pg_dump custom-format file (the operator decrypts the
    ``.dump.age`` backup offline first — the server never sees age
    keys) and performs a verify-then-swap restore. Returns the
    verification summary.

    Requires super_admin role.
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can restore the database",
        )

    settings = get_settings()
    if not settings.RESTORE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Database restore is disabled on this deployment "
            "(RESTORE_ENABLED=false)",
        )

    if db._engine is None or db._engine.dialect.name != "postgresql":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Database restore requires a PostgreSQL deployment",
        )

    if not file.filename or not file.filename.lower().endswith(".dump"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a .dump (pg_dump custom-format) archive",
        )

    dump = await file.read()
    if len(dump) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty",
        )
    if len(dump) > MAX_RESTORE_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "File too large. Maximum size is "
                f"{MAX_RESTORE_UPLOAD_SIZE // (1024 * 1024)} MB"
            ),
        )

    current_db = _current_database_name()
    if confirmation.strip() != current_db:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Confirmation text must be the current database name: {current_db}",
        )

    if _restore_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A restore is already in progress",
        )

    async with _restore_lock:
        try:
            return await restore_from_dump(dump, operator_id=user_id)
        except RestoreError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
            ) from exc


def _current_database_name() -> str:
    """Database name from the live engine's URL (the confirm text)."""
    if db._engine is None:
        return ""
    return db._engine.url.database or "postgres"
