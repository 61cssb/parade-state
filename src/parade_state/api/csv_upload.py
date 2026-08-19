"""CSV upload API endpoints."""

import csv
import hashlib
import io

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parade_state.db import get_db_session
from parade_state.models import (
    AuditLog,
    ColumnMetadata,
    CsvUpload,
    NominalRoll,
    Personnel,
    Tagging,
    User,
)
from parade_state.models.schemas import (
    CsvUploadListItem,
    CsvUploadProcessRequest,
    CsvUploadProcessResponse,
    CsvUploadProcessUnmatchedItem,
    CsvUploadResponse,
)
from parade_state.utils import ranks, utc_dt
from parade_state.utils.csv_constants import (
    CANONICAL_MAP,
    EXTRA_KEY_FOR_INDEX,
    INFERRED_TYPES,
    coerce_int,
    is_integer_column,
    parse_caa_date,
    snake,
)
from parade_state.api.tagging import _load_nr_tagging, copy_entries_by_pers_no

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
    auto_process: bool = Query(
        False,
        description=(
            "Attempt to process the upload into a NominalRoll immediately "
            "after storing it. On success the result is in process_result; "
            "on failure the reason is in process_error and the upload "
            "remains stored/unprocessed for the manual Step 2 flow."
        ),
    ),
    db: AsyncSession = Depends(get_db_session),
) -> CsvUploadResponse:
    """Upload a CSV file for ingestion.

    Accepts a CSV file, computes SHA256 hash, checks for duplicates,
    parses headers and counts lines, stores raw content in CsvUpload,
    and creates an AuditLog entry.

    With ``auto_process=true`` the stored upload is immediately run
    through the same pipeline as ``POST /csv/{id}/process`` (creating a
    NominalRoll + Personnel + the NR's 1:1 empty Tagging) whenever
    validation passes; any processing failure is reported via
    ``process_error`` without failing the upload itself.

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
            original_filename=existing_upload.original_filename,
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
        original_filename=file.filename,
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
            original_filename=existing_upload.original_filename,
            line_count=existing_upload.line_count,
            detected_columns=detected_columns,
            status=existing_upload.status,
            uploaded_at=existing_upload.uploaded_at,
            uploaded_by=existing_upload.uploaded_by,
            is_duplicate=True,
        )

    await db.refresh(upload)

    # Capture the ORM values before auto-processing: a processing
    # failure rolls the session back, which expires the instance
    # (attribute access would then need IO in a sync context).
    upload_id = upload.id
    upload_hash = upload.sha256_hash
    upload_filename = upload.original_filename
    upload_line_count = upload.line_count
    upload_status = upload.status
    upload_uploaded_at = upload.uploaded_at

    process_result: CsvUploadProcessResponse | None = None
    process_error: str | None = None
    if auto_process:
        try:
            process_result = await _process_upload_into_nr(
                db, upload, CsvUploadProcessRequest(created_by=user_id)
            )
        except HTTPException as exc:
            # The upload is stored and committed; only the processing
            # failed. Report the reason and leave the upload for the
            # manual Step 2 flow.
            await db.rollback()
            process_error = str(exc.detail)

    return CsvUploadResponse(
        id=upload_id,
        sha256_hash=upload_hash,
        original_filename=upload_filename,
        line_count=upload_line_count,
        detected_columns=detected_columns,
        status=upload_status,
        uploaded_at=upload_uploaded_at,
        uploaded_by=user_id,
        is_duplicate=False,
        process_result=process_result,
        process_error=process_error,
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
            CsvUpload.original_filename,
            CsvUpload.line_count,
            CsvUpload.status,
            CsvUpload.uploaded_at,
            CsvUpload.uploaded_by,
            CsvUpload.nominal_roll_id,
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
            original_filename=row.original_filename,
            line_count=row.line_count,
            status=row.status,
            uploaded_at=row.uploaded_at,
            uploaded_by=row.uploaded_by,
            nominal_roll_id=row.nominal_roll_id,
            mapping_confirmed_at=row.mapping_confirmed_at,
            diff_confirmed_at=row.diff_confirmed_at,
        )
        for row in rows
    ]


# ----------------------------------------------------------------------------
# CSV → Nominal Roll processing
# ----------------------------------------------------------------------------


def _decode_raw(raw_bytes: bytes) -> str:
    """Decode raw CSV bytes (UTF-8 with Latin-1 fallback)."""
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("latin-1")


def _parse_csv_rows(raw_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    """Parse raw CSV bytes into (header, data_rows)."""
    text = _decode_raw(raw_bytes)
    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)
    if not all_rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file contains no data",
        )
    return all_rows[0], all_rows[1:]


@router.post(
    "/{upload_id}/process",
    response_model=CsvUploadProcessResponse,
    status_code=status.HTTP_201_CREATED,
)
async def process_csv_upload(
    upload_id: str,
    payload: CsvUploadProcessRequest,
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> CsvUploadProcessResponse:
    """Process a stored CsvUpload into a full NominalRoll pipeline.

    Reads ``CsvUpload.raw_content``, parses CAA date from the original
    filename, and inserts a NominalRoll + Personnel + ColumnMetadata +
    auto-created empty Tagging. Links the upload to the new NR via
    ``CsvUpload.nominal_roll_id``.

    When ``source_nominal_roll_id`` is provided, copies the source NR's
    tagging entries into the new NR's tagging by ``pers_no`` matching.
    Personnel in the source tagging with no pers_no match in the new NR
    are surfaced in the response.

    Requires admin or super_admin role.
    """
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can process CSV uploads",
        )

    # Load the upload.
    upload = (
        await db.execute(select(CsvUpload).where(CsvUpload.id == upload_id))
    ).scalar_one_or_none()
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CSV upload not found: {upload_id}",
        )

    return await _process_upload_into_nr(db, upload, payload)


async def _process_upload_into_nr(
    db: AsyncSession, upload: CsvUpload, payload: CsvUploadProcessRequest
) -> CsvUploadProcessResponse:
    """Core CSV → NominalRoll pipeline, shared by the process endpoint
    and the upload endpoint's auto-processing.

    Raises ``HTTPException`` (400/409) with a user-facing reason when the
    upload cannot be processed; the caller decides whether that is an
    error response or an auto-processing report.
    """
    if upload.nominal_roll_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"CSV upload {upload.id} has already been processed into "
                f"nominal roll {upload.nominal_roll_id}."
            ),
        )

    # CAA date from filename.
    if not upload.original_filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot parse CAA date: upload has no original_filename.",
        )
    try:
        caa_date = parse_caa_date(upload.original_filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot parse CAA date from filename "
                f"{upload.original_filename!r}. Expected a 'caaYYMMDD' token."
            ),
        ) from exc

    # Refuse if an NR with this CAA already exists.
    existing_nr = (
        await db.execute(select(NominalRoll).where(NominalRoll.caa == caa_date))
    ).scalar_one_or_none()
    if existing_nr is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Nominal roll with CAA {caa_date.isoformat()} already exists.",
        )

    # Parse CSV rows.
    header, data_rows = _parse_csv_rows(upload.raw_content)
    if len(header) != len(CANONICAL_MAP):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"CSV header has {len(header)} columns; expected "
                f"{len(CANONICAL_MAP)} per the canonical mapping."
            ),
        )

    created_by = payload.created_by

    # Create the NominalRoll (personnel_count set after Personnel insert).
    nominal_roll = NominalRoll(
        caa=caa_date,
        csv_hash=upload.sha256_hash,
        personnel_count=0,
        uploaded_by=created_by,
        notes=f"Processed from CSV upload {upload.original_filename}",
    )
    db.add(nominal_roll)
    await db.flush()

    # Per-roll column metadata.
    for idx, raw_name, canonical, _ in CANONICAL_MAP:
        original_label = raw_name if raw_name else "(empty header)"
        if raw_name == "Remarks":
            original_label = f"Remarks (column {idx + 1})"
        db.add(
            ColumnMetadata(
                nominal_roll_id=nominal_roll.id,
                csv_upload_id=upload.id,
                original_name=original_label,
                canonical_name=canonical,
                inferred_type=INFERRED_TYPES.get(raw_name, "string"),
                is_required=canonical in {"rank", "full_name", "unit"},
            )
        )

    # Personnel rows.
    inserted_personnel = 0
    skipped_rows: list[dict[str, str | int]] = []
    for row_num, row in enumerate(data_rows, start=2):
        if len(row) < len(CANONICAL_MAP):
            continue  # malformed row (too few columns)
        core_values: dict[str, str] = {}
        extra_fields: dict[str, str | int | None] = {}
        for idx, raw_name, canonical, goes_to_extra in CANONICAL_MAP:
            value = row[idx].strip()
            if canonical and not goes_to_extra:
                core_values[canonical] = value
            elif goes_to_extra:
                key = EXTRA_KEY_FOR_INDEX.get(idx, snake(raw_name))
                if is_integer_column(raw_name):
                    extra_fields[key] = coerce_int(value)
                else:
                    extra_fields[key] = value or None

        rank_value = core_values.get("rank") or ""
        try:
            category = ranks.category_for_rank(rank_value)
        except ValueError:
            skipped_rows.append(
                {
                    "row": row_num,
                    "rank": rank_value,
                    "full_name": core_values.get("full_name") or "",
                }
            )
            continue

        db.add(
            Personnel(
                nominal_roll_id=nominal_roll.id,
                pers_no=core_values.get("pers_no") or None,
                rank=rank_value,
                category=category,
                full_name=core_values.get("full_name") or "",
                unit=core_values.get("unit") or "",
                sub_unit_1=core_values.get("sub_unit_1") or None,
                sub_unit_2=core_values.get("sub_unit_2") or None,
                sub_unit_3=core_values.get("sub_unit_3") or None,
                extra_fields=extra_fields,
                status="active",
                created_by=created_by,
            )
        )
        inserted_personnel += 1

    nominal_roll.personnel_count = inserted_personnel

    # Auto-create the 1:1 tagging for this NR.
    tagging = Tagging(
        label=f"Tagging for CAA {caa_date.isoformat()}",
        nominal_roll_id=nominal_roll.id,
        created_by=created_by,
    )
    db.add(tagging)
    await db.flush()

    # Optional: copy entries from a source NR's tagging.
    matched_count = 0
    unmatched: list[CsvUploadProcessUnmatchedItem] = []
    if payload.source_nominal_roll_id is not None:
        if payload.source_nominal_roll_id == nominal_roll.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source_nominal_roll_id must differ from the new nominal roll.",
            )
        source_tagging = await _load_nr_tagging(
            db, payload.source_nominal_roll_id, with_entries=True
        )
        if source_tagging is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "Source nominal roll "
                    f"{payload.source_nominal_roll_id} has no tagging."
                ),
            )
        # Re-load the new tagging with the entries collection materialized
        # (db.refresh does not populate relationships; copy_entries_by_pers_no
        # reads .entries which would otherwise lazy-load outside async ctx).
        tagging = (
            await db.execute(
                select(Tagging)
                .where(Tagging.id == tagging.id)
                .options(selectinload(Tagging.entries))
            )
        ).scalar_one()
        matched_count, raw_unmatched = await copy_entries_by_pers_no(
            db, source_tagging, tagging, nominal_roll.id
        )
        unmatched = [
            CsvUploadProcessUnmatchedItem(pers_no=u.pers_no, name=u.name)
            for u in raw_unmatched
        ]
        if matched_count:
            tagging.updated_at = utc_dt.ensure_naive(utc_dt.utcnow())
            tagging.updated_by = created_by

    # Link the upload to the new NR.
    upload.nominal_roll_id = nominal_roll.id

    audit_log = AuditLog(
        user_id=created_by,
        entity_type="nominal_roll",
        entity_id=nominal_roll.id,
        action="create",
        description=(
            f"Processed CSV upload {upload.original_filename!r} into nominal "
            f"roll CAA {caa_date.isoformat()}: {inserted_personnel} personnel "
            f"inserted, {len(skipped_rows)} skipped, "
            f"{matched_count} tagging entries imported."
        ),
    )
    db.add(audit_log)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="CSV processing failed (constraint violation).",
        ) from exc

    return CsvUploadProcessResponse(
        nominal_roll_id=nominal_roll.id,
        personnel_inserted=inserted_personnel,
        rows_skipped=len(skipped_rows),
        tagging_entries_imported=matched_count,
        unmatched=unmatched,
    )
