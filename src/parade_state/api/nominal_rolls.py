"""Nominal Roll API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import CsvUpload, NominalRoll
from parade_state.models.schemas import (
    NominalRollListItem,
    NominalRollResponse,
    NominalRollUpdate,
)
from parade_state.utils import utc_dt

router = APIRouter()


@router.get("", response_model=list[NominalRollListItem])
async def list_nominal_rolls(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> list[NominalRollListItem]:
    """List nominal rolls with their latest linked CsvUpload's filename.

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    # Subquery: most recent CsvUpload per nominal roll (by uploaded_at).
    latest_upload = (
        select(
            CsvUpload.nominal_roll_id.label("nominal_roll_id"),
            CsvUpload.original_filename.label("original_filename"),
        )
        .where(CsvUpload.nominal_roll_id.is_not(None))
        .order_by(CsvUpload.nominal_roll_id, CsvUpload.uploaded_at.desc())
        .subquery()
    )

    query = (
        select(
            NominalRoll.id,
            NominalRoll.caa,
            NominalRoll.status,
            NominalRoll.personnel_count,
            NominalRoll.uploaded_at,
            NominalRoll.uploaded_by,
            NominalRoll.csv_hash,
            NominalRoll.label,
            NominalRoll.remarks,
            latest_upload.c.original_filename,
        )
        .outerjoin(
            latest_upload, latest_upload.c.nominal_roll_id == NominalRoll.id
        )
        .order_by(NominalRoll.uploaded_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if status_filter:
        query = query.where(NominalRoll.status == status_filter)

    rows = (await db.execute(query)).all()

    return [
        NominalRollListItem(
            id=row.id,
            caa=row.caa,
            status=row.status,
            personnel_count=row.personnel_count,
            uploaded_at=row.uploaded_at,
            uploaded_by=row.uploaded_by,
            csv_hash=row.csv_hash,
            original_filename=row.original_filename,
            label=row.label,
            remarks=row.remarks,
        )
        for row in rows
    ]


@router.get("/{nominal_roll_id}", response_model=NominalRollResponse)
async def get_nominal_roll(
    nominal_roll_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> NominalRollResponse:
    """Fetch a single nominal roll by id with its latest CsvUpload's filename.

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    row = await _load_nominal_roll_with_filename(db, nominal_roll_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {nominal_roll_id}",
        )

    return _row_to_response(row)


@router.patch("/{nominal_roll_id}", response_model=NominalRollResponse)
async def update_nominal_roll(
    nominal_roll_id: str,
    update_data: NominalRollUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> NominalRollResponse:
    """Update a nominal roll (status transitions, notes, label, remarks).

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    result = await db.execute(
        select(NominalRoll).where(NominalRoll.id == nominal_roll_id)
    )
    nominal_roll = result.scalar_one_or_none()
    if not nominal_roll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {nominal_roll_id}",
        )

    if update_data.status == "confirmed":
        if nominal_roll.status != "draft":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Only draft nominal rolls can be confirmed "
                    f"(current status: '{nominal_roll.status}')."
                ),
            )
        nominal_roll.status = "confirmed"
        nominal_roll.confirmed_at = utc_dt.utcnow()
        nominal_roll.confirmed_by = user_id

    elif update_data.status == "draft":
        if nominal_roll.status != "confirmed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Only confirmed nominal rolls can be reverted to draft "
                    f"(current status: '{nominal_roll.status}')."
                ),
            )
        nominal_roll.status = "draft"
        nominal_roll.confirmed_at = None
        nominal_roll.confirmed_by = None

    if update_data.notes is not None:
        nominal_roll.notes = update_data.notes

    if update_data.label is not None:
        nominal_roll.label = update_data.label

    if update_data.remarks is not None:
        nominal_roll.remarks = update_data.remarks

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Label already in use by another nominal roll.",
        ) from None

    row = await _load_nominal_roll_with_filename(db, nominal_roll_id)
    return _row_to_response(row)


@router.delete("/{nominal_roll_id}")
async def delete_nominal_roll(
    nominal_roll_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a nominal roll and cascade-delete all dependent data.

    Requires super_admin role. Only draft or confirmed nominal rolls can be
    deleted. Cascades to personnel, deployments, sessions, attendance records,
    and related data.
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can delete nominal rolls",
        )

    result = await db.execute(
        select(NominalRoll).where(NominalRoll.id == nominal_roll_id)
    )
    nominal_roll = result.scalar_one_or_none()
    if not nominal_roll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {nominal_roll_id}",
        )

    if nominal_roll.status not in ("draft", "confirmed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Can only delete draft or confirmed nominal rolls "
                f"(current status: '{nominal_roll.status}')."
            ),
        )

    await db.delete(nominal_roll)
    await db.commit()

    return {"detail": f"Nominal roll {nominal_roll_id} deleted"}


async def _load_nominal_roll_with_filename(
    db: AsyncSession, nominal_roll_id: str
):
    """Fetch a single nominal roll row joined with its latest CsvUpload's filename."""
    latest_upload = (
        select(
            CsvUpload.nominal_roll_id.label("nominal_roll_id"),
            CsvUpload.original_filename.label("original_filename"),
        )
        .where(CsvUpload.nominal_roll_id == nominal_roll_id)
        .order_by(CsvUpload.uploaded_at.desc())
        .limit(1)
        .subquery()
    )
    return (
        await db.execute(
            select(
                NominalRoll.id,
                NominalRoll.caa,
                NominalRoll.status,
                NominalRoll.personnel_count,
                NominalRoll.uploaded_at,
                NominalRoll.uploaded_by,
                NominalRoll.csv_hash,
                NominalRoll.label,
                NominalRoll.remarks,
                NominalRoll.notes,
                NominalRoll.confirmed_at,
                NominalRoll.confirmed_by,
                NominalRoll.created_at,
                latest_upload.c.original_filename,
            ).outerjoin(
                latest_upload, latest_upload.c.nominal_roll_id == NominalRoll.id
            )
        )
    ).one_or_none()


def _row_to_response(row) -> NominalRollResponse:
    """Build a NominalRollResponse from a joined query row."""
    return NominalRollResponse(
        id=row.id,
        caa=row.caa,
        status=row.status,
        personnel_count=row.personnel_count,
        uploaded_at=row.uploaded_at,
        uploaded_by=row.uploaded_by,
        csv_hash=row.csv_hash,
        original_filename=row.original_filename,
        label=row.label,
        remarks=row.remarks,
        notes=row.notes,
        confirmed_at=row.confirmed_at,
        confirmed_by=row.confirmed_by,
        created_at=row.created_at,
    )


def _require_admin(user_role: str) -> None:
    if user_role not in ("admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can view nominal rolls",
        )
