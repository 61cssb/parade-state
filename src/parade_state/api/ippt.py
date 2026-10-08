"""IPPT monitoring API endpoints (local-testing-only feature; FEATURE_IPPT).

- ``POST /api/v1/ippt/snapshots`` (super-admin): ingest the six-file
  report snapshot for one report date — atomic per date, replace-on-
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
"""

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.dependencies import require_admin_user, require_super_admin_user
from parade_state.db import get_db_session
from parade_state.models import AuditLog, User
from parade_state.services import ippt as ippt_service
from parade_state.services.ippt import IngestError

router = APIRouter()


class ExclusionRequest(BaseModel):
    """Why a serviceman is being removed from tracking (required)."""

    reason: str


@router.post("/snapshots")
async def upload_snapshots(
    files: list[UploadFile] = File(
        ...,
        description="The six monitoring reports (.csv or .xlsx) for one report date",
    ),
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Ingest the six-file snapshot (super-admin-only).

    All six files must be present with canonical
    ``{REPORT}_{STATE}_{YYYYMMDD}.csv``/``.xlsx`` names carrying the same
    date (the set may mix the two extensions); anything else rejects the
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
