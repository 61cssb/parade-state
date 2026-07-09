"""Estab API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import CsvUpload, Estab
from parade_state.models.schemas import EstabListItem, EstabResponse, EstabUpdate
from parade_state.utils import utc_dt

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
            Estab.label,
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
            label=row.label,
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

    row = await _load_estab_with_filename(db, estab_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Estab not found: {estab_id}",
        )

    return _row_to_response(row)


@router.patch("/{estab_id}", response_model=EstabResponse)
async def update_estab(
    estab_id: str,
    update_data: EstabUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> EstabResponse:
    """Update an estab (status transitions, notes, and label).

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    result = await db.execute(select(Estab).where(Estab.id == estab_id))
    estab = result.scalar_one_or_none()
    if not estab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Estab not found: {estab_id}",
        )

    if update_data.status == "confirmed":
        if estab.status != "draft":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Only draft estabs can be confirmed "
                    f"(current status: '{estab.status}')."
                ),
            )
        estab.status = "confirmed"
        estab.confirmed_at = utc_dt.utcnow()
        estab.confirmed_by = user_id

    elif update_data.status == "draft":
        if estab.status != "confirmed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Only confirmed estabs can be reverted to draft "
                    f"(current status: '{estab.status}')."
                ),
            )
        estab.status = "draft"
        estab.confirmed_at = None
        estab.confirmed_by = None

    if update_data.notes is not None:
        estab.notes = update_data.notes

    if update_data.label is not None:
        estab.label = update_data.label

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Label already in use by another estab.",
        ) from None

    row = await _load_estab_with_filename(db, estab_id)
    return _row_to_response(row)


@router.delete("/{estab_id}")
async def delete_estab(
    estab_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete an estab and cascade-delete all dependent data.

    Requires super_admin role. Only draft or confirmed estabs can be deleted.
    Cascades to personnel, deployments, sessions, attendance records, and
    related data.
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can delete estabs",
        )

    result = await db.execute(select(Estab).where(Estab.id == estab_id))
    estab = result.scalar_one_or_none()
    if not estab:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Estab not found: {estab_id}",
        )

    if estab.status not in ("draft", "confirmed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Can only delete draft or confirmed estabs "
                f"(current status: '{estab.status}')."
            ),
        )

    await db.delete(estab)
    await db.commit()

    return {"detail": f"Estab {estab_id} deleted"}


async def _load_estab_with_filename(db: AsyncSession, estab_id: str):
    """Fetch a single estab row joined with its latest CsvUpload's filename."""
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
    return (
        await db.execute(
            select(
                Estab.id,
                Estab.caa,
                Estab.status,
                Estab.personnel_count,
                Estab.uploaded_at,
                Estab.uploaded_by,
                Estab.csv_hash,
                Estab.label,
                Estab.notes,
                Estab.confirmed_at,
                Estab.confirmed_by,
                Estab.created_at,
                latest_upload.c.original_filename,
            ).outerjoin(latest_upload, latest_upload.c.estab_id == Estab.id)
        )
    ).one_or_none()


def _row_to_response(row) -> EstabResponse:
    """Build an EstabResponse from a joined query row."""
    return EstabResponse(
        id=row.id,
        caa=row.caa,
        status=row.status,
        personnel_count=row.personnel_count,
        uploaded_at=row.uploaded_at,
        uploaded_by=row.uploaded_by,
        csv_hash=row.csv_hash,
        original_filename=row.original_filename,
        label=row.label,
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
