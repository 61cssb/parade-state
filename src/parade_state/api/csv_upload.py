"""CSV upload API endpoints."""

import csv
import hashlib
import io

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import AuditLog, CsvUpload, User
from parade_state.models.schemas import CsvUploadListItem, CsvUploadResponse

router = APIRouter()

# Maximum upload size: 10 MB
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


def _parse_csv_columns(raw_bytes: bytes) -> tuple[list[str], int]:
    """Parse CSV header row and count data lines.

    Returns (detected_columns, line_count) where line_count excludes the header.
    """
    try:
        content_str = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content_str = raw_bytes.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File encoding not supported. Please use UTF-8 encoded CSV.",
            ) from None

    reader = csv.reader(io.StringIO(content_str))
    rows = list(reader)

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file contains no data",
        )

    detected_columns = rows[0]
    line_count = max(len(rows) - 1, 0)

    return detected_columns, line_count


@router.post("/upload", response_model=CsvUploadResponse)
async def upload_csv(
    file: UploadFile,
    user_id: str = Query(..., description="User ID uploading the file"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> CsvUploadResponse:
    """Upload a CSV file for ingestion.

    Accepts a CSV file, computes SHA256 hash, checks for duplicates,
    parses headers and counts lines, stores raw content in CsvUpload,
    and creates an AuditLog entry.

    Requires admin or super_admin role.
    """
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can upload CSV files",
        )

    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must have a .csv extension",
        )

    raw_bytes = await file.read()

    if len(raw_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty",
        )

    if len(raw_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024 * 1024)} MB",
        )

    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

    # Check for duplicate
    existing_result = await db.execute(
        select(CsvUpload).where(CsvUpload.sha256_hash == sha256_hash)
    )
    existing_upload = existing_result.scalar_one_or_none()

    if existing_upload:
        detected_columns, _ = _parse_csv_columns(existing_upload.raw_content)
        return CsvUploadResponse(
            id=existing_upload.id,
            sha256_hash=existing_upload.sha256_hash,
            line_count=existing_upload.line_count,
            detected_columns=detected_columns,
            status=existing_upload.status,
            uploaded_at=existing_upload.uploaded_at,
            uploaded_by=existing_upload.uploaded_by,
            is_duplicate=True,
        )

    detected_columns, line_count = _parse_csv_columns(raw_bytes)

    upload = CsvUpload(
        raw_content=raw_bytes,
        sha256_hash=sha256_hash,
        line_count=line_count,
        uploaded_by=user_id,
    )
    db.add(upload)
    await db.flush()

    audit_log = AuditLog(
        user_id=user_id,
        entity_type="csv_upload",
        entity_id=upload.id,
        action="create",
        description=f"Uploaded CSV file '{file.filename}' with {line_count} data rows and {len(detected_columns)} columns",
    )
    db.add(audit_log)

    try:
        await db.commit()
    except IntegrityError:
        # Race condition: same hash uploaded concurrently
        await db.rollback()
        existing_result = await db.execute(
            select(CsvUpload).where(CsvUpload.sha256_hash == sha256_hash)
        )
        existing_upload = existing_result.scalar_one()
        detected_columns, _ = _parse_csv_columns(existing_upload.raw_content)
        return CsvUploadResponse(
            id=existing_upload.id,
            sha256_hash=existing_upload.sha256_hash,
            line_count=existing_upload.line_count,
            detected_columns=detected_columns,
            status=existing_upload.status,
            uploaded_at=existing_upload.uploaded_at,
            uploaded_by=existing_upload.uploaded_by,
            is_duplicate=True,
        )

    await db.refresh(upload)

    return CsvUploadResponse(
        id=upload.id,
        sha256_hash=upload.sha256_hash,
        line_count=upload.line_count,
        detected_columns=detected_columns,
        status=upload.status,
        uploaded_at=upload.uploaded_at,
        uploaded_by=upload.uploaded_by,
        is_duplicate=False,
    )


@router.get("/uploads", response_model=list[CsvUploadListItem])
async def list_csv_uploads(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> list[CsvUploadListItem]:
    """List recent CSV uploads (metadata only, no raw_content).

    Returns a paginated list ordered by uploaded_at desc.

    Requires admin or super_admin role.
    """
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can view CSV uploads",
        )

    query = (
        select(
            CsvUpload.id,
            CsvUpload.sha256_hash,
            CsvUpload.line_count,
            CsvUpload.status,
            CsvUpload.uploaded_at,
            CsvUpload.uploaded_by,
            CsvUpload.estab_id,
            CsvUpload.mapping_confirmed_at,
            CsvUpload.diff_confirmed_at,
        )
        .order_by(CsvUpload.uploaded_at.desc())
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    return [
        CsvUploadListItem(
            id=row.id,
            sha256_hash=row.sha256_hash,
            line_count=row.line_count,
            status=row.status,
            uploaded_at=row.uploaded_at,
            uploaded_by=row.uploaded_by,
            estab_id=row.estab_id,
            mapping_confirmed_at=row.mapping_confirmed_at,
            diff_confirmed_at=row.diff_confirmed_at,
        )
        for row in rows
    ]
