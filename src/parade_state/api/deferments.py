"""Deferment API endpoints.

Super-admin-only CRUD for personnel deferments. Creating/approving a deferment
drives the linked personnel's ``callup_status`` field.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import Deferment, Personnel
from parade_state.models.schemas import (
    DefermentCreate,
    DefermentResponse,
    DefermentUpdate,
)
from parade_state.utils import utc_dt

router = APIRouter()


# ============================================================================
# Constants & helpers
# ============================================================================

# Deferment statuses that belong to a later workflow phase. Setting a deferment
# to either of these does NOT update personnel.callup_status.
_DEFERMENT_STATUSES_NEUTRAL = {"Not called up", "Do not call up"}


def _require_super_admin(user_role: str) -> None:
    """Authorize super_admin only."""
    if user_role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only super admins can manage deferments",
        )


def _apply_callup_transition(
    personnel: Personnel,
    old_status: str | None,
    new_status: str | None,
) -> None:
    """Transition personnel.callup_status based on deferment status change.

    Called on PATCH (``old_status`` → ``new_status``) and DELETE
    (``new_status`` passed as ``None``).
    """
    # Neutral statuses are a separate workflow phase — never touch callup_status.
    if new_status in _DEFERMENT_STATUSES_NEUTRAL:
        return

    if new_status == "Approved":
        personnel.callup_status = "Deferred"
    elif old_status == "Approved":
        # Moving away from Approved (to a non-neutral status, or via delete)
        # → revert to Called Up.
        personnel.callup_status = "Called Up"
    # else: no Approved involvement → callup_status unchanged


def _snapshot_sub_unit(personnel: Personnel) -> str | None:
    """First non-empty sub-unit value from the personnel record."""
    for value in (personnel.sub_unit_1, personnel.sub_unit_2, personnel.sub_unit_3):
        if value:
            return value
    return None


def _to_response(deferment: Deferment, estab_id: str | None = None) -> DefermentResponse:
    """Build a DefermentResponse from a Deferment ORM instance."""
    return DefermentResponse(
        id=deferment.id,
        personnel_id=deferment.personnel_id,
        estab_id=estab_id,
        rank_name=deferment.rank_name,
        sub_unit=deferment.sub_unit,
        reason=deferment.reason,
        status=deferment.status,
        remarks=deferment.remarks,
        oc_updates=deferment.oc_updates,
        created_at=deferment.created_at,
        created_by=deferment.created_by,
        updated_at=deferment.updated_at,
        updated_by=deferment.updated_by,
    )


# ============================================================================
# Endpoints
# ============================================================================


@router.get("", response_model=list[DefermentResponse])
async def list_deferments(
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    personnel_id: str | None = Query(None),
    estab_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    reason: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> list[DefermentResponse]:
    """List deferments, optionally filtered by personnel / estab / status / reason.

    Requires super_admin role. ``estab_id`` filters via the deferment's linked
    personnel record.
    """
    _require_super_admin(user_role)

    query = (
        select(Deferment, Personnel.estab_id)
        .join(Personnel, Deferment.personnel_id == Personnel.id)
        .order_by(Deferment.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if personnel_id:
        query = query.where(Deferment.personnel_id == personnel_id)
    if estab_id:
        query = query.where(Personnel.estab_id == estab_id)
    if status_filter:
        query = query.where(Deferment.status == status_filter)
    if reason:
        query = query.where(Deferment.reason == reason)

    rows = (await db.execute(query)).all()
    return [_to_response(d, estab_id=eid) for d, eid in rows]


@router.post("", response_model=DefermentResponse, status_code=http_status.HTTP_201_CREATED)
async def create_deferment(
    payload: DefermentCreate,
    user_id: str = Query(..., description="User ID creating the deferment"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> DefermentResponse:
    """Create a new deferment.

    Snapshots ``rank_name`` (``{rank} {full_name}``) and ``sub_unit`` from the
    linked personnel at creation time. New deferments start with
    ``status="Pending action"`` so callup_status is not affected.
    """
    _require_super_admin(user_role)

    result = await db.execute(
        select(Personnel).where(Personnel.id == payload.personnel_id)
    )
    personnel = result.scalar_one_or_none()
    if personnel is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Personnel not found: {payload.personnel_id}",
        )
    if personnel.status != "active":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot create deferment for non-active personnel "
                f"(current status: '{personnel.status}')."
            ),
        )

    deferment = Deferment(
        personnel_id=personnel.id,
        rank_name=f"{personnel.rank} {personnel.full_name}".strip(),
        sub_unit=_snapshot_sub_unit(personnel),
        reason=payload.reason,
        status="Pending action",
        remarks=payload.remarks,
        oc_updates=payload.oc_updates,
        created_by=user_id,
    )
    db.add(deferment)
    await db.commit()
    await db.refresh(deferment)

    return _to_response(deferment, estab_id=personnel.estab_id)


@router.get("/{deferment_id}", response_model=DefermentResponse)
async def get_deferment(
    deferment_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> DefermentResponse:
    """Fetch a single deferment by id."""
    _require_super_admin(user_role)

    row = (
        await db.execute(
            select(Deferment, Personnel.estab_id)
            .join(Personnel, Deferment.personnel_id == Personnel.id)
            .where(Deferment.id == deferment_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Deferment not found: {deferment_id}",
        )
    deferment, estab_id = row
    return _to_response(deferment, estab_id=estab_id)


@router.patch("/{deferment_id}", response_model=DefermentResponse)
async def update_deferment(
    deferment_id: str,
    payload: DefermentUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> DefermentResponse:
    """Update a deferment.

    Status changes drive the linked personnel's ``callup_status`` via
    ``_apply_callup_transition``.
    """
    _require_super_admin(user_role)

    result = await db.execute(
        select(Deferment).where(Deferment.id == deferment_id)
    )
    deferment = result.scalar_one_or_none()
    if deferment is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Deferment not found: {deferment_id}",
        )

    old_status = deferment.status

    if payload.reason is not None:
        deferment.reason = payload.reason
    if payload.status is not None:
        deferment.status = payload.status
    if payload.remarks is not None:
        deferment.remarks = payload.remarks
    if payload.oc_updates is not None:
        deferment.oc_updates = payload.oc_updates

    new_status = deferment.status

    # Drive callup_status on the linked personnel if anything could change.
    if payload.status is not None:
        personnel_result = await db.execute(
            select(Personnel).where(Personnel.id == deferment.personnel_id)
        )
        personnel = personnel_result.scalar_one_or_none()
        if personnel is not None:
            _apply_callup_transition(personnel, old_status, new_status)

    deferment.updated_at = utc_dt.ensure_naive(utc_dt.utcnow())
    deferment.updated_by = user_id

    await db.commit()
    await db.refresh(deferment)

    # Reload with estab_id for response.
    row = (
        await db.execute(
            select(Deferment, Personnel.estab_id)
            .join(Personnel, Deferment.personnel_id == Personnel.id)
            .where(Deferment.id == deferment_id)
        )
    ).one()
    deferment, estab_id = row
    return _to_response(deferment, estab_id=estab_id)


@router.delete("/{deferment_id}")
async def delete_deferment(
    deferment_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a deferment.

    If the deferment was Approved, revert the linked personnel's
    ``callup_status`` to ``Called Up``.
    """
    _require_super_admin(user_role)

    result = await db.execute(
        select(Deferment).where(Deferment.id == deferment_id)
    )
    deferment = result.scalar_one_or_none()
    if deferment is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Deferment not found: {deferment_id}",
        )

    # Revert callup_status if this deferment was Approved (treat delete as
    # transitioning to None — Approved → None reverts to Called Up).
    if deferment.status == "Approved":
        personnel_result = await db.execute(
            select(Personnel).where(Personnel.id == deferment.personnel_id)
        )
        personnel = personnel_result.scalar_one_or_none()
        if personnel is not None:
            _apply_callup_transition(personnel, deferment.status, None)

    await db.delete(deferment)
    await db.commit()

    return {"detail": f"Deferment {deferment_id} deleted"}
