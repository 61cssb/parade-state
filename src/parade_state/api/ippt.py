"""IPPT monitoring API endpoints (local-testing-only feature; FEATURE_IPPT).

- ``POST /api/v1/ippt/snapshots`` (super-admin): ingest the six-file
  report snapshot for one report date — atomic per date, replace-on-
  re-ingest, row-level rejects quarantined (docs/IPPT_MONITORING.md §4).
- ``GET /api/v1/ippt/dashboard`` (admin+): latest state per serviceman,
  3/6/9-month tier highlights, and summary counts.
- ``GET /api/v1/ippt/snapshots`` (admin+): per-date ingest history.
- ``GET /api/v1/ippt/quarantine`` (admin+): the reject list for review.
- ``GET /api/v1/ippt/window/{personnel_id}`` (admin+): one person's
  window, trajectory, and screening history.
"""

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.dependencies import require_admin_user, require_super_admin_user
from parade_state.db import get_db_session
from parade_state.models import AuditLog, User
from parade_state.services import ippt as ippt_service
from parade_state.services.ippt import IngestError

router = APIRouter()


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
    ``GET /api/v1/ippt/quarantine``.
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
