"""Deferment API endpoints.

Super-admin-only CRUD for personnel deferments. Deferment status changes
drive the linked personnel's ``inpro_status`` field (issue 32): approving
prompts in the UI (the API leaves inpro_status untouched), while moving an
approved deferment to any other status — or deleting it — always reverts
the person to ``yet_to_inpro``.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.dependencies import require_super_admin_user
from parade_state.db import get_db_session
from parade_state.models import Deferment, Personnel, User
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

# Deferment statuses that belong to a later workflow phase ("Not called
# up" / "Do not call up"). They no longer gate the inpro transition: an
# approved deferment reverting to these still resets inpro_status.


def _apply_inpro_transition(
    personnel: Personnel,
    old_status: str | None,
    new_status: str | None,
) -> None:
    """Transition personnel.inpro_status based on a deferment status change.

    Called on PATCH (``old_status`` → ``new_status``) and DELETE
    (``new_status`` passed as ``None``).

    Issue 32 semantics:

    - Approving (``new_status == "Approved"``) does NOT touch inpro_status —
      the admin UI prompts "set Inpro status to Deferred?" and PATCHes the
      personnel separately if confirmed, so declining leaves it unchanged.
    - Moving away from Approved (to any other status, or via delete) ALWAYS
      reverts to ``yet_to_inpro`` — even if the person was manually marked
      ``inproed`` in the meantime.
    """
    if old_status == "Approved" and new_status != "Approved":
        personnel.inpro_status = "yet_to_inpro"


def _snapshot_sub_unit(personnel: Personnel) -> str | None:
    """First non-empty sub-unit value from the personnel record."""
    for value in (personnel.sub_unit_1, personnel.sub_unit_2, personnel.sub_unit_3):
        if value:
            return value
    return None


def _to_response(
    deferment: Deferment, nominal_roll_id: str | None = None
) -> DefermentResponse:
    """Build a DefermentResponse from a Deferment ORM instance."""
    return DefermentResponse(
        id=deferment.id,
        personnel_id=deferment.personnel_id,
        nominal_roll_id=nominal_roll_id,
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
    user: User = Depends(require_super_admin_user),
    personnel_id: str | None = Query(None),
    nominal_roll_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    reason: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> list[DefermentResponse]:
    """List deferments, optionally filtered by personnel / nominal roll / status / reason.

    Requires super_admin role. ``nominal_roll_id`` filters via the deferment's
    linked personnel record.
    """

    query = (
        select(Deferment, Personnel.nominal_roll_id)
        .join(Personnel, Deferment.personnel_id == Personnel.id)
        .order_by(Deferment.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if personnel_id:
        query = query.where(Deferment.personnel_id == personnel_id)
    if nominal_roll_id:
        query = query.where(Personnel.nominal_roll_id == nominal_roll_id)
    if status_filter:
        query = query.where(Deferment.status == status_filter)
    if reason:
        query = query.where(Deferment.reason == reason)

    rows = (await db.execute(query)).all()
    return [_to_response(d, nominal_roll_id=eid) for d, eid in rows]


@router.post("", response_model=DefermentResponse, status_code=http_status.HTTP_201_CREATED)
async def create_deferment(
    payload: DefermentCreate,
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> DefermentResponse:
    """Create a new deferment.

    Snapshots ``rank_name`` (``{rank} {full_name}``) and ``sub_unit`` from the
    linked personnel at creation time. New deferments start with
    ``status="Pending action"`` so inpro_status is not affected.
    """
    user_id = str(user.id)

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

    return _to_response(deferment, nominal_roll_id=personnel.nominal_roll_id)


@router.get("/{deferment_id}", response_model=DefermentResponse)
async def get_deferment(
    deferment_id: str,
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> DefermentResponse:
    """Fetch a single deferment by id."""

    row = (
        await db.execute(
            select(Deferment, Personnel.nominal_roll_id)
            .join(Personnel, Deferment.personnel_id == Personnel.id)
            .where(Deferment.id == deferment_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Deferment not found: {deferment_id}",
        )
    deferment, nominal_roll_id = row
    return _to_response(deferment, nominal_roll_id=nominal_roll_id)


@router.patch("/{deferment_id}", response_model=DefermentResponse)
async def update_deferment(
    deferment_id: str,
    payload: DefermentUpdate,
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> DefermentResponse:
    """Update a deferment.

    Status changes drive the linked personnel's ``inpro_status`` via
    ``_apply_inpro_transition`` (approval leaves it to the UI prompt;
    leaving Approved reverts to ``yet_to_inpro``).
    """
    user_id = str(user.id)

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

    # Drive inpro_status on the linked personnel if anything could change.
    if payload.status is not None:
        personnel_result = await db.execute(
            select(Personnel).where(Personnel.id == deferment.personnel_id)
        )
        personnel = personnel_result.scalar_one_or_none()
        if personnel is not None:
            _apply_inpro_transition(personnel, old_status, new_status)

    deferment.updated_at = utc_dt.ensure_naive(utc_dt.utcnow())
    deferment.updated_by = user_id

    await db.commit()
    await db.refresh(deferment)

    # Reload with nominal_roll_id for response.
    row = (
        await db.execute(
            select(Deferment, Personnel.nominal_roll_id)
            .join(Personnel, Deferment.personnel_id == Personnel.id)
            .where(Deferment.id == deferment_id)
        )
    ).one()
    deferment, nominal_roll_id = row
    return _to_response(deferment, nominal_roll_id=nominal_roll_id)


@router.delete("/{deferment_id}")
async def delete_deferment(
    deferment_id: str,
    user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a deferment.

    If the deferment was Approved, revert the linked personnel's
    ``inpro_status`` to ``yet_to_inpro``.
    """

    result = await db.execute(
        select(Deferment).where(Deferment.id == deferment_id)
    )
    deferment = result.scalar_one_or_none()
    if deferment is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Deferment not found: {deferment_id}",
        )

    # Revert inpro_status if this deferment was Approved (treat delete as
    # transitioning to None — Approved → anything reverts to yet_to_inpro).
    if deferment.status == "Approved":
        personnel_result = await db.execute(
            select(Personnel).where(Personnel.id == deferment.personnel_id)
        )
        personnel = personnel_result.scalar_one_or_none()
        if personnel is not None:
            _apply_inpro_transition(personnel, deferment.status, None)

    await db.delete(deferment)
    await db.commit()

    return {"detail": f"Deferment {deferment_id} deleted"}
