"""IPPT monitoring API endpoints (FEATURE_IPPT-gated; the Railway
production environment force-disables the flag, see config.Settings).

- ``POST /api/v1/ippt/snapshots`` (super-admin): ingest the report
  snapshot for one report date — the six canonical files or the single
  All Tabs workbook — atomic per date, replace-on-
  re-ingest, row-level rejects quarantined (docs/IPPT_MONITORING.md §4);
  the response reports window-consistency warnings and excluded
  servicemen who reappeared (decided 2026-10-08).
- ``GET /api/v1/ippt/dashboard`` (admin+): latest state per serviceman,
  3/6/9-month tier highlights, and summary counts.
- ``GET /api/v1/ippt/snapshots`` (admin+): per-date ingest history.
- ``GET /api/v1/ippt/quarantine`` (admin+): the reject list for review.
- ``GET /api/v1/ippt/window/{personnel_id}`` (admin+): one person's
  window, trajectory, and screening history.
- ``POST``/``DELETE /api/v1/ippt/servicemen/{serviceman_id}/exclusion``
  (super-admin): remove-from-tracking with a reason, and re-include —
  both audit-logged (decided 2026-10-08).
- ``POST /api/v1/ippt/defaulters`` (super-admin): ingest the quarterly
  defaulter release (CSV or first-tab XLSX) for a user-chosen workyear
  quarter (Q1 = Apr–Jun … Q4 = Jan–Mar); replace-on-re-ingest of the
  current quarter, archiving the previously current one, audit-logged
  (docs/IPPT_MONITORING.md §7).
- ``GET /api/v1/ippt/defaulters`` (admin+): the current quarter's
  report header + Pending rows (what the dashboard table shows).
- ``PATCH /api/v1/ippt/defaulters/rows/{row_id}`` (admin+): set/clear
  one row's reason and remarks; current-quarter rows only, audit-logged.
"""

import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.dependencies import require_admin_user, require_super_admin_user
from parade_state.db import get_db_session
from parade_state.models import AuditLog, User
from parade_state.services import defaulter as defaulter_service
from parade_state.services import ippt as ippt_service
from parade_state.services.defaulter import (
    DefaulterIngestError,
    DefaulterRowArchived,
    DefaulterRowError,
    DefaulterRowNotFound,
)
from parade_state.services.ippt import IngestError

router = APIRouter()


class ExclusionRequest(BaseModel):
    """Why a serviceman is being removed from tracking (required)."""

    reason: str


class DefaulterRowUpdate(BaseModel):
    """Reason/remarks edit: omit a field (or ``null``) to leave it
    unchanged, send an empty string to clear it."""

    reason: Literal["hsp", "attitude_issue", "uncontactable", "others", ""] | None = (
        None
    )
    remarks: str | None = None


@router.post("/snapshots")
async def upload_snapshots(
    files: list[UploadFile] = File(
        ...,
        description=(
            "The six monitoring reports (.csv or .xlsx, canonical names) "
            "for one report date, or the single All_Tabs_Export_YYYYMMDD.xlsx "
            "workbook"
        ),
    ),
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Ingest the report snapshot (super-admin-only).

    Either the six canonical ``{REPORT}_{STATE}_{YYYYMMDD}.csv``/``
    .xlsx`` files carrying the same date (the set may mix the two
    extensions) or the single ``All_Tabs_Export_YYYYMMDD.xlsx`` workbook
    (all six tabs); anything else rejects the
    whole submission with 422 and nothing is written. Re-uploading a
    report date replaces its previous ingest.
    Row-level validation failures do not reject the file — they land in
    the quarantine list, reported here and reviewable at
    ``GET /api/v1/ippt/quarantine``. Window-consistency warnings and
    reappeared excluded servicemen are reported here too (decided
    2026-10-08) — the rows themselves ingest normally.
    """
    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        payloads.append((upload.filename or "", await upload.read()))

    try:
        result = await ippt_service.ingest_snapshot(db, payloads)
    except IngestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.add(
        AuditLog(
            user_id=str(user.id),
            entity_type="ippt_snapshot",
            entity_id=result.report_date.isoformat(),
            action="create",
            changes=None,
            description=json.dumps(
                {
                    "action": "ingest_snapshot",
                    "report_date": result.report_date.isoformat(),
                    "replaced": result.replaced,
                    "rows": result.total_rows,
                    "quarantined": result.quarantined_total,
                    "match": result.match_counts,
                    "window_warnings": result.window_warnings,
                    "excluded_reappeared": [
                        entry["full_name"] for entry in result.excluded_reappeared
                    ],
                }
            ),
        )
    )
    await db.commit()

    return {
        "report_date": result.report_date.isoformat(),
        "replaced": result.replaced,
        "total_rows": result.total_rows,
        "quarantined": result.quarantined_total,
        "per_kind": result.per_kind,
        "match": result.match_counts,
        "window_warnings": result.window_warnings,
        "excluded_reappeared": result.excluded_reappeared,
    }


@router.post("/servicemen/{serviceman_id}/exclusion")
async def exclude_serviceman(
    serviceman_id: int,
    body: ExclusionRequest,
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a serviceman from tracking (super-admin-only), with a
    required reason. Rows and history are retained; the dashboard and
    tier views hide the person, and a later snapshot that shows them
    again surfaces them in the upload response. Audit-logged."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A reason is required to remove a serviceman from tracking",
        )
    serviceman = await ippt_service.set_tracking_exclusion(
        db, serviceman_id, excluded=True, reason=reason
    )
    if serviceman is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such IPPT serviceman",
        )
    db.add(
        AuditLog(
            user_id=str(user.id),
            entity_type="ippt_serviceman",
            entity_id=str(serviceman.id),
            action="update",
            changes=None,
            description=json.dumps(
                {
                    "action": "exclude",
                    "full_name": serviceman.full_name,
                    "reason": reason,
                }
            ),
        )
    )
    await db.commit()
    return {
        "serviceman_id": serviceman.id,
        "excluded": True,
        "exclusion_reason": reason,
    }


@router.delete("/servicemen/{serviceman_id}/exclusion")
async def reinclude_serviceman(
    serviceman_id: int,
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Re-include a serviceman previously removed from tracking
    (super-admin-only, audit-logged)."""
    serviceman = await ippt_service.set_tracking_exclusion(
        db, serviceman_id, excluded=False
    )
    if serviceman is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such IPPT serviceman",
        )
    db.add(
        AuditLog(
            user_id=str(user.id),
            entity_type="ippt_serviceman",
            entity_id=str(serviceman.id),
            action="update",
            changes=None,
            description=json.dumps(
                {
                    "action": "include",
                    "full_name": serviceman.full_name,
                }
            ),
        )
    )
    await db.commit()
    return {
        "serviceman_id": serviceman.id,
        "excluded": False,
        "exclusion_reason": None,
    }


@router.get("/dashboard")
async def dashboard(
    user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Latest state per serviceman, tier highlights, and summary counts."""
    return await ippt_service.dashboard_data(db)


@router.get("/snapshots")
async def snapshot_history(
    user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Per-report-date ingest history (newest first)."""
    return await ippt_service.snapshot_history(db)


@router.get("/quarantine")
async def quarantine(
    user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Quarantined report rows awaiting review (newest first)."""
    return await ippt_service.quarantined_rows(db)


@router.get("/window/{personnel_id}")
async def window_detail(
    personnel_id: str,
    user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """One person's IPPT window, trajectory, and screening history."""
    detail = await ippt_service.window_detail(db, personnel_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No IPPT data is linked to this personnel id",
        )
    return detail


# ============================================================================
# Defaulters (§7): quarterly release ingest + row annotations
# ============================================================================


@router.post("/defaulters")
async def upload_defaulters(
    file: UploadFile = File(
        ...,
        description=(
            "The quarterly defaulter release: .csv, or an .xlsx whose "
            "first tab carries the data"
        ),
    ),
    year: int = Form(
        ...,
        description=(
            "Workyear label: the calendar year the workyear started, "
            "e.g. 2026 for Apr 2026 – Mar 2027"
        ),
    ),
    quarter: int = Form(
        ..., description="Workyear quarter 1–4 (Q1 = Apr–Jun … Q4 = Jan–Mar)"
    ),
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Ingest one quarterly defaulter release (super-admin-only).

    The quarter is user-supplied — the file does not carry it. Re-uploading
    the current quarter replaces it (reason/remarks annotations on its rows
    are dropped with them); any other quarter archives the previously
    current one; re-uploading an archived quarter is rejected. Row-level
    validation failures quarantine (reported here, reviewable on the
    upload page) instead of rejecting the file.
    """
    content = await file.read()
    try:
        result = await defaulter_service.ingest_defaulter_report(
            db, file.filename or "", content, year, quarter
        )
    except DefaulterIngestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.add(
        AuditLog(
            user_id=str(user.id),
            entity_type="defaulter_report",
            entity_id=str(result.report_id),
            action="create",
            changes=None,
            description=json.dumps(
                {
                    "action": "ingest_defaulter_report",
                    "quarter": result.label,
                    "file": file.filename,
                    "replaced": result.replaced,
                    "archived": result.archived_labels,
                    "rows": result.total_rows,
                    "quarantined": result.quarantined_total,
                    "match": result.match_counts,
                }
            ),
        )
    )
    await db.commit()

    return {
        "report": {
            "id": result.report_id,
            "label": result.label,
            "year": result.year,
            "quarter": result.quarter,
        },
        "replaced": result.replaced,
        "archived": result.archived_labels,
        "pending_rows": result.pending_rows,
        "archived_rows": result.archived_rows,
        "quarantined": result.quarantined_total,
        "match": result.match_counts,
        "unmatched_names": result.unmatched_names,
        "ambiguous_names": result.ambiguous_names,
    }


@router.get("/defaulters")
async def get_defaulters(
    user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The current quarter's report header and Pending rows (empty when
    nothing has been ingested yet)."""
    payload = await defaulter_service.current_defaulters(db)
    return payload or {"report": None, "rows": []}


@router.patch("/defaulters/rows/{row_id}")
async def update_defaulter_row(
    row_id: int,
    body: DefaulterRowUpdate,
    user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Set or clear one defaulter row's reason and remarks (admin+).

    Only current-quarter rows are editable. Audit-logged with the
    previous values so the change is reconstructible."""
    try:
        row = await defaulter_service.update_defaulter_row(
            db, row_id, body.reason, body.remarks
        )
    except DefaulterRowNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except DefaulterRowArchived as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except DefaulterRowError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    db.add(
        AuditLog(
            user_id=str(user.id),
            entity_type="defaulter_row",
            entity_id=str(row.id),
            action="update",
            changes=None,
            description=json.dumps(
                {
                    "action": "update_defaulter_row",
                    "full_name": row.full_name,
                    "reason": row.reason,
                    "remarks": row.remarks,
                }
            ),
        )
    )
    await db.commit()
    return {
        "id": row.id,
        "reason": row.reason,
        "remarks": row.remarks,
    }
