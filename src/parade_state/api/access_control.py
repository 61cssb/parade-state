"""Access control management API endpoints.

NR-scoped Subunit-1 attendance assignments (issue #4 PR 2). The old
grouping-scoped access grants and subunit scopes were removed with the
groupings redesign (issue 26) — the new grouping model has no
per-grouping scoping.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import (
    NominalRoll,
    User,
    UserSubunitAssignment,
)
from parade_state.models.schemas import (
    UserSubunitAssignmentCreate,
    UserSubunitAssignmentResponse,
)

router = APIRouter()


# ============================================================================
# User Subunit-1 Assignments (NR-scoped attendance access — issue #4 PR 2)
# ============================================================================


def _require_super_admin(role: str) -> None:
    """Authorize super_admin only."""
    if role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only super admins can manage Subunit-1 assignments",
        )


async def _load_nr_or_404(db: AsyncSession, nominal_roll_id: str) -> NominalRoll:
    nr = (
        await db.execute(
            select(NominalRoll).where(NominalRoll.id == nominal_roll_id)
        )
    ).scalar_one_or_none()
    if nr is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {nominal_roll_id}",
        )
    return nr


@router.post(
    "/nominal-rolls/{nominal_roll_id}/users/{user_id}/subunit-assignments",
    response_model=UserSubunitAssignmentResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def grant_subunit_assignment(
    nominal_roll_id: str,
    user_id: str,
    payload: UserSubunitAssignmentCreate,
    granted_by: str = Query(..., description="User ID granting the assignment"),
    user_role: str = Query(..., description="Role of granting user"),
    db: AsyncSession = Depends(get_db_session),
):
    """Grant a user attendance-update rights for one sub_unit_1 on an NR.

    Super-admin only. ``sub_unit_1`` must match a value present on the NR's
    roster (case-sensitive).
    """
    _require_super_admin(user_role)
    await _load_nr_or_404(db, nominal_roll_id)

    target = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    existing = (
        await db.execute(
            select(UserSubunitAssignment).where(
                UserSubunitAssignment.user_id == user_id,
                UserSubunitAssignment.nominal_roll_id == nominal_roll_id,
                UserSubunitAssignment.sub_unit_1 == payload.sub_unit_1,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="User already assigned to this sub_unit_1 on this nominal roll",
        )

    assignment = UserSubunitAssignment(
        user_id=user_id,
        nominal_roll_id=nominal_roll_id,
        sub_unit_1=payload.sub_unit_1,
        created_by=granted_by,
    )
    db.add(assignment)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="User already assigned to this sub_unit_1 on this nominal roll",
        ) from None
    await db.refresh(assignment)
    return assignment


@router.get(
    "/nominal-rolls/{nominal_roll_id}/subunit-assignments",
    response_model=list[UserSubunitAssignmentResponse],
)
async def list_subunit_assignments_for_nr(
    nominal_roll_id: str,
    requesting_user_id: str = Query(..., description="User ID making the request"),
    requesting_user_role: str = Query(..., description="Role of requesting user"),
    db: AsyncSession = Depends(get_db_session),
):
    """List all Subunit-1 assignments on an NR.

    Super-admin sees all. Other users see only their own assignments.
    """
    await _load_nr_or_404(db, nominal_roll_id)
    query = select(UserSubunitAssignment).where(
        UserSubunitAssignment.nominal_roll_id == nominal_roll_id
    )
    if requesting_user_role != "super_admin":
        query = query.where(
            UserSubunitAssignment.user_id == requesting_user_id
        )
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get(
    "/users/{user_id}/subunit-assignments",
    response_model=list[UserSubunitAssignmentResponse],
)
async def list_subunit_assignments_for_user(
    user_id: str,
    requesting_user_id: str = Query(..., description="User ID making the request"),
    requesting_user_role: str = Query(..., description="Role of requesting user"),
    db: AsyncSession = Depends(get_db_session),
):
    """List a user's Subunit-1 assignments across all NRs.

    Users see only their own. Super-admin sees any user.
    """
    if requesting_user_id != user_id and requesting_user_role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You can only view your own Subunit-1 assignments",
        )
    result = await db.execute(
        select(UserSubunitAssignment).where(
            UserSubunitAssignment.user_id == user_id
        )
    )
    return list(result.scalars().all())


@router.delete(
    "/nominal-rolls/{nominal_roll_id}/users/{user_id}/subunit-assignments/{assignment_id}"
)
async def revoke_subunit_assignment(
    nominal_roll_id: str,
    user_id: str,
    assignment_id: str,
    revoked_by: str = Query(..., description="User ID revoking the assignment"),
    user_role: str = Query(..., description="Role of revoking user"),
    db: AsyncSession = Depends(get_db_session),
):
    """Revoke a Subunit-1 assignment. Super-admin only."""
    _require_super_admin(user_role)
    assignment = (
        await db.execute(
            select(UserSubunitAssignment).where(
                UserSubunitAssignment.id == assignment_id,
                UserSubunitAssignment.nominal_roll_id == nominal_roll_id,
                UserSubunitAssignment.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Subunit-1 assignment not found",
        )
    await db.delete(assignment)
    await db.commit()
    return {"detail": "Subunit-1 assignment revoked"}
