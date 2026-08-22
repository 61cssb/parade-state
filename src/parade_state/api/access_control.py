"""Access control management API endpoints.

Scope grants (issues #4 and #28): each ``UserSubunitAssignment`` row is a
(unit, sub_unit_1) pair on a Nominal Roll, with the explicit ``*``
sentinel marking a wildcard column. Grants are managed by super-admins
only; the /admin/users view is the UI for this API.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.api.subunit_access import WILDCARD
from parade_state.db import get_db_session
from parade_state.models import (
    NominalRoll,
    Personnel,
    User,
    UserSubunitAssignment,
)
from parade_state.models.schemas import (
    UserSubunitAssignmentCreate,
    UserSubunitAssignmentResponse,
)

router = APIRouter()


# ============================================================================
# Scope grants (NR-scoped access — issues #4 and #28)
# ============================================================================


def _require_super_admin(role: str) -> None:
    """Authorize super_admin only."""
    if role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only super admins can manage scope assignments",
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


def _nr_display_label(nr: NominalRoll) -> str:
    """Human label for grouping grants in the admin UI."""
    if nr.label:
        return nr.label
    return nr.caa.isoformat() if nr.caa else str(nr.id)[:8]


async def _roster_locations(
    db: AsyncSession, nominal_roll_id: str
) -> set[tuple[str, str | None]]:
    """Distinct (unit, sub_unit_1) pairs present on the NR's roster."""
    result = await db.execute(
        select(Personnel.unit, Personnel.sub_unit_1).where(
            Personnel.nominal_roll_id == nominal_roll_id
        )
    )
    return {(unit, sub1) for unit, sub1 in result.all()}


def _validate_grant_shape(payload: UserSubunitAssignmentCreate) -> None:
    """Reject empty values and the double-wildcard grant."""
    if not payload.unit or not payload.sub_unit_1:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Grant values must not be empty — use '*' for a wildcard",
        )
    if payload.unit == WILDCARD and payload.sub_unit_1 == WILDCARD:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot grant unit='*' together with sub_unit_1='*' — "
                "grant the specific units instead"
            ),
        )


async def _validate_grant_against_roster(
    db: AsyncSession, nominal_roll_id: str, payload: UserSubunitAssignmentCreate
) -> None:
    """Concrete grant values must exist on the NR's roster (case-sensitive)."""
    locations = await _roster_locations(db, nominal_roll_id)
    units = {unit for unit, _ in locations}

    if payload.unit != WILDCARD and payload.unit not in units:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Unit not present on this nominal roll: {payload.unit}",
        )

    if payload.sub_unit_1 != WILDCARD:
        if payload.unit != WILDCARD:
            sub1s_in_unit = {sub1 for unit, sub1 in locations if unit == payload.unit}
        else:
            sub1s_in_unit = {sub1 for _, sub1 in locations}
        if payload.sub_unit_1 not in sub1s_in_unit:
            scope = f"unit {payload.unit}" if payload.unit != WILDCARD else "the roster"
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Sub-unit 1 not present under {scope}: "
                    f"{payload.sub_unit_1}"
                ),
            )


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
    """Grant a user scope for one (unit, sub_unit_1) pair on an NR.

    Super-admin only. ``*`` wildcards a column (unit='*' = any unit,
    sub_unit_1='*' = every sub-unit of the unit), but both must not be
    wildcards. Concrete values must match values present on the NR's
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

    _validate_grant_shape(payload)
    await _validate_grant_against_roster(db, nominal_roll_id, payload)

    existing = (
        await db.execute(
            select(UserSubunitAssignment).where(
                UserSubunitAssignment.user_id == user_id,
                UserSubunitAssignment.nominal_roll_id == nominal_roll_id,
                UserSubunitAssignment.unit == payload.unit,
                UserSubunitAssignment.sub_unit_1 == payload.sub_unit_1,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="User already has this scope grant on this nominal roll",
        )

    assignment = UserSubunitAssignment(
        user_id=user_id,
        nominal_roll_id=nominal_roll_id,
        unit=payload.unit,
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
            detail="User already has this scope grant on this nominal roll",
        ) from None
    await db.refresh(assignment)
    return assignment


@router.get(
    "/nominal-rolls/{nominal_roll_id}/scope-options",
)
async def nr_scope_options(
    nominal_roll_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="Role of requesting user"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Units and unit→sub_unit_1 values present on an NR's roster.

    Feeds the grant form's dropdowns in /admin/users (super-admin only).
    A personnel row with a NULL sub_unit_1 contributes the unit but no
    sub-unit value — such personnel are covered by sub_unit_1='*'
    grants, not by a NULL-valued grant.
    """
    _require_super_admin(user_role)
    await _load_nr_or_404(db, nominal_roll_id)

    locations = await _roster_locations(db, nominal_roll_id)
    units = sorted({unit for unit, _ in locations})
    subunits_by_unit: dict[str, list[str]] = {}
    for unit, sub1 in locations:
        if sub1 is not None:
            subunits_by_unit.setdefault(unit, []).append(sub1)
    for unit in subunits_by_unit:
        subunits_by_unit[unit] = sorted(set(subunits_by_unit[unit]))

    return {"units": units, "subunits_by_unit": subunits_by_unit}


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
    """List all scope grants on an NR (with the NR's display label).

    Super-admin sees all. Other users see only their own grants.
    """
    nr = await _load_nr_or_404(db, nominal_roll_id)
    query = select(UserSubunitAssignment).where(
        UserSubunitAssignment.nominal_roll_id == nominal_roll_id
    )
    if requesting_user_role != "super_admin":
        query = query.where(
            UserSubunitAssignment.user_id == requesting_user_id
        )
    result = await db.execute(query)
    label = _nr_display_label(nr)
    return [
        UserSubunitAssignmentResponse.model_validate(assignment).model_copy(
            update={"nominal_roll_label": label}
        )
        for assignment in result.scalars().all()
    ]


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
    """List a user's scope grants across all NRs (with display labels).

    Users see only their own. Super-admin sees any user.
    """
    if requesting_user_id != user_id and requesting_user_role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You can only view your own scope assignments",
        )
    result = await db.execute(
        select(UserSubunitAssignment, NominalRoll)
        .where(UserSubunitAssignment.user_id == user_id)
        .outerjoin(
            NominalRoll,
            NominalRoll.id == UserSubunitAssignment.nominal_roll_id,
        )
    )
    return [
        UserSubunitAssignmentResponse.model_validate(assignment).model_copy(
            update={"nominal_roll_label": _nr_display_label(nr)}
        )
        for assignment, nr in result.all()
    ]


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
    """Revoke a scope grant. Super-admin only."""
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
            detail="Scope assignment not found",
        )
    await db.delete(assignment)
    await db.commit()
    return {"detail": "Scope assignment revoked"}
