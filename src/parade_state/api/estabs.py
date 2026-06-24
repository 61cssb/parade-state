"""Estab API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import CsvUpload, Estab
from parade_state.models.schemas import EstabListItem, EstabResponse

router = APIRouter()


@router.get("", response_model=list[EstabListItem])
async def list_estabs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> list[EstabListItem]:
    """List estabs with their latest linked CsvUpload's filename.

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    # Subquery: most recent CsvUpload per estab (by uploaded_at).
    latest_upload = (
        select(
            CsvUpload.estab_id.label("estab_id"),
            CsvUpload.original_filename.label("original_filename"),
        )
        .where(CsvUpload.estab_id.is_not(None))
        .order_by(CsvUpload.estab_id, CsvUpload.uploaded_at.desc())
        .subquery()
    )

    query = (
        select(
            Estab.id,
            Estab.caa,
            Estab.status,
            Estab.personnel_count,
            Estab.uploaded_at,
            Estab.uploaded_by,
            Estab.csv_hash,
            latest_upload.c.original_filename,
        )
        .outerjoin(latest_upload, latest_upload.c.estab_id == Estab.id)
        .order_by(Estab.uploaded_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if status_filter:
        query = query.where(Estab.status == status_filter)

    rows = (await db.execute(query)).all()

    return [
        EstabListItem(
            id=row.id,
            caa=row.caa,
            status=row.status,
            personnel_count=row.personnel_count,
            uploaded_at=row.uploaded_at,
            uploaded_by=row.uploaded_by,
            csv_hash=row.csv_hash,
            original_filename=row.original_filename,
        )
        for row in rows
    ]


@router.get("/{estab_id}", response_model=EstabResponse)
async def get_estab(
    estab_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> EstabResponse:
    """Fetch a single estab by id with its latest CsvUpload's filename.

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    latest_upload = (
        select(
            CsvUpload.estab_id.label("estab_id"),
            CsvUpload.original_filename.label("original_filename"),
        )
        .where(CsvUpload.estab_id == estab_id)
        .order_by(CsvUpload.uploaded_at.desc())
        .limit(1)
        .subquery()
    )

    row = (
        await db.execute(
            select(
                Estab.id,
                Estab.caa,
                Estab.status,
                Estab.personnel_count,
                Estab.uploaded_at,
                Estab.uploaded_by,
                Estab.csv_hash,
                Estab.notes,
                Estab.confirmed_at,
                Estab.confirmed_by,
                Estab.created_at,
                latest_upload.c.original_filename,
            ).outerjoin(latest_upload, latest_upload.c.estab_id == Estab.id)
        )
    ).one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Estab not found: {estab_id}",
        )

    return EstabResponse(
        id=row.id,
        caa=row.caa,
        status=row.status,
        personnel_count=row.personnel_count,
        uploaded_at=row.uploaded_at,
        uploaded_by=row.uploaded_by,
        csv_hash=row.csv_hash,
        original_filename=row.original_filename,
        notes=row.notes,
        confirmed_at=row.confirmed_at,
        confirmed_by=row.confirmed_by,
        created_at=row.created_at,
    )


def _require_admin(user_role: str) -> None:
    if user_role not in ("admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can view estabs",
        )
