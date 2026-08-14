"""Grouping management API endpoints.

"Grouping" is the umbrella term covering standard operational groupings,
adhoc groupings, and vehicle manifests.
"""

import csv

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models.attendance import PRESENT_LIKE_STATUSES, Attendance
from parade_state.models.csv_ingestion import NominalRoll
from parade_state.models.grouping import (
    Grouping,
    GroupingNotes,
    GroupingPersonnelExclusion,
    GroupingPersonnelOverride,
)
from parade_state.models.personnel import Personnel
from parade_state.models.schemas import (
    ExclusionCreate,
    GroupingCreate,
    GroupingListParams,
    GroupingNotesCreate,
    GroupingNotesResponse,
    GroupingNotesUpdate,
    GroupingPersonnelOverrideCreate,
    GroupingPersonnelOverrideResponse,
    GroupingResponse,
    GroupingStatusResponse,
    GroupingStatusSessionInfo,
    GroupingStatusUnitBreakdown,
    GroupingUpdate,
)
from parade_state.utils import utc_dt

router = APIRouter()


# ============================================================================
# Helper Functions
# ============================================================================


async def verify_grouping_access(
    grouping_id: str,
    user_id: str,
    user_role: str,
    db: AsyncSession,
) -> Grouping:
    """Verify user has access to grouping and return it."""
    # Super admins have full access
    if user_role == "super_admin":
        result = await db.execute(
            select(Grouping).where(Grouping.id == grouping_id)
        )
        grouping = result.scalar_one_or_none()
        if not grouping:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Grouping not found",
            )
        return grouping

    # For regular users and admins, check grouping access
    # TODO: Implement proper access control based on user scopes
    # For now, admins can access all groupings
    if user_role in ["admin", "user"]:
        result = await db.execute(
            select(Grouping).where(Grouping.id == grouping_id)
        )
        grouping = result.scalar_one_or_none()
        if not grouping:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Grouping not found",
            )
        return grouping

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions to access this grouping",
    )


async def validate_grouping_status_transition(
    current_status: str,
    new_status: str,
) -> bool:
    """Validate grouping status transition is allowed."""
    valid_transitions = {
        "draft": ["active", "inactive", "archived"],
        "active": ["inactive", "closed"],
        "inactive": ["active", "archived", "closed"],
        "closed": ["finalized"],
        "finalized": [],  # Finalized is terminal
        "archived": [],  # Archived is terminal
    }

    return new_status in valid_transitions.get(current_status, [])


# ============================================================================
# Grouping CRUD Endpoints
# ============================================================================


@router.post(
    "/", response_model=GroupingResponse, status_code=status.HTTP_201_CREATED
)
async def create_grouping(
    grouping_data: GroupingCreate,
    user_id: str = Query(..., description="User ID creating the grouping"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new grouping.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can create groupings",
        )

    # Verify nominal roll exists and is confirmed
    result = await db.execute(
        select(NominalRoll).where(NominalRoll.id == grouping_data.nominal_roll_id)
    )
    nominal_roll = result.scalar_one_or_none()
    if not nominal_roll:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nominal roll {grouping_data.nominal_roll_id} not found",
        )
    if nominal_roll.status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot create grouping from nominal roll in '{nominal_roll.status}' status. "
                "Nominal roll must be confirmed."
            ),
        )

    # Validate date range — required for standard mode, optional for adhoc/vehicle
    if grouping_data.mode == "standard":
        if not grouping_data.valid_from or not grouping_data.valid_until:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="valid_from and valid_until are required for standard groupings",
            )
        if grouping_data.valid_until <= grouping_data.valid_from:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="valid_until must be after valid_from",
            )

    # Create grouping
    grouping = Grouping(
        name=grouping_data.name,
        nominal_roll_id=grouping_data.nominal_roll_id,
        mode=grouping_data.mode,
        status=grouping_data.status,
        valid_from=grouping_data.valid_from,
        valid_until=grouping_data.valid_until,
        scheduled_activation=grouping_data.scheduled_activation,
        notes=grouping_data.notes,
        created_by=user_id,
    )

    # Auto-activate if status is active
    if grouping_data.status == "active":
        grouping.activated_at = utc_dt.utcnow()

    db.add(grouping)
    await db.commit()
    await db.refresh(grouping)

    return grouping


@router.get("/", response_model=list[GroupingResponse])
async def list_groupings(
    status: str | None = None,
    nominal_roll_id: str | None = None,
    search: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for filtering"),
    db: AsyncSession = Depends(get_db_session),
):
    """List groupings with optional filtering.

    All authenticated users can list groupings.
    Filters may be applied based on user role.
    """
    query = select(Grouping)

    # Apply filters
    if status:
        query = query.where(Grouping.status == status)

    if nominal_roll_id:
        query = query.where(Grouping.nominal_roll_id == nominal_roll_id)

    if search:
        search_pattern = f"%{search}%"
        query = query.where(Grouping.name.ilike(search_pattern))

    # Order by created_at descending
    query = query.order_by(Grouping.created_at.desc())

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    groupings = result.scalars().all()

    return groupings


@router.get("/{grouping_id}", response_model=GroupingResponse)
async def get_grouping(
    grouping_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a specific grouping by ID.

    Requires appropriate access permissions.
    """
    grouping = await verify_grouping_access(grouping_id, user_id, user_role, db)
    return grouping


@router.patch("/{grouping_id}", response_model=GroupingResponse)
async def update_grouping(
    grouping_id: str,
    update_data: GroupingUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update a grouping.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can update groupings",
        )

    # Get grouping
    grouping = await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Validate date range if both provided
    if update_data.valid_from and update_data.valid_until:
        if update_data.valid_until <= update_data.valid_from:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="valid_until must be after valid_from",
            )

    # Update fields
    if update_data.name is not None:
        grouping.name = update_data.name

    if update_data.valid_from is not None:
        grouping.valid_from = update_data.valid_from

    if update_data.valid_until is not None:
        grouping.valid_until = update_data.valid_until

    if update_data.scheduled_activation is not None:
        grouping.scheduled_activation = update_data.scheduled_activation

    if update_data.notes is not None:
        grouping.notes = update_data.notes

    # Handle status transition
    if update_data.status is not None:
        current_status = grouping.status
        new_status = update_data.status

        # Validate transition
        if not await validate_grouping_status_transition(current_status, new_status):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status transition from {current_status} to {new_status}",
            )

        # Handle activation
        if new_status == "active" and current_status != "active":
            # Check if another grouping is already active
            active_result = await db.execute(
                select(Grouping).where(
                    and_(
                        Grouping.status == "active",
                        Grouping.id != grouping_id,
                    )
                )
            )
            active_grouping = active_result.scalar_one_or_none()

            if active_grouping:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Another grouping is already active. Only one grouping can be active at a time.",
                )

            grouping.activated_at = utc_dt.utcnow()

        # Handle deactivation
        if new_status in ["inactive", "closed"] and current_status == "active":
            grouping.deactivated_at = utc_dt.utcnow()

        grouping.status = new_status

    await db.commit()
    await db.refresh(grouping)

    return grouping


@router.delete("/{grouping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_grouping(
    grouping_id: str,
    user_id: str = Query(..., description="User ID making the deletion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a grouping.

    Requires super_admin role.
    """
    # Verify user has permission
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can delete groupings",
        )

    # Get grouping
    grouping = await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Prevent deletion of active or finalized groupings
    if grouping.status in ["active", "finalized"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete grouping with status {grouping.status}",
        )

    await db.delete(grouping)
    await db.commit()

    return None


# ============================================================================
# Grouping Activation Endpoints
# ============================================================================


@router.post("/{grouping_id}/activate", response_model=GroupingResponse)
async def activate_grouping(
    grouping_id: str,
    user_id: str = Query(..., description="User ID activating the grouping"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Manually activate a grouping.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can activate groupings",
        )

    # Get grouping
    grouping = await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Check if already active
    if grouping.status == "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Grouping is already active",
        )

    # Check if another grouping is already active
    active_result = await db.execute(
        select(Grouping).where(
            and_(
                Grouping.status == "active",
                Grouping.id != grouping_id,
            )
        )
    )
    active_grouping = active_result.scalar_one_or_none()

    if active_grouping:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Another grouping is already active. Only one grouping can be active at a time.",
        )

    # Validate transition
    if not await validate_grouping_status_transition(grouping.status, "active"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot activate grouping with status {grouping.status}",
        )

    # Activate grouping
    grouping.status = "active"
    grouping.activated_at = utc_dt.utcnow()

    await db.commit()
    await db.refresh(grouping)

    return grouping


@router.post("/{grouping_id}/deactivate", response_model=GroupingResponse)
async def deactivate_grouping(
    grouping_id: str,
    user_id: str = Query(..., description="User ID deactivating the grouping"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Manually deactivate a grouping.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can deactivate groupings",
        )

    # Get grouping
    grouping = await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Check if currently active
    if grouping.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active groupings can be deactivated",
        )

    # Deactivate grouping
    grouping.status = "inactive"
    grouping.deactivated_at = utc_dt.utcnow()

    await db.commit()
    await db.refresh(grouping)

    return grouping


# ============================================================================
# Grouping Personnel Exclusions
# ============================================================================


@router.post(
    "/{grouping_id}/exclusions",
    status_code=status.HTTP_201_CREATED,
)
async def create_exclusion(
    grouping_id: str,
    exclusion_data: ExclusionCreate,
    user_id: str = Query(..., description="User ID creating the exclusion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Exclude a personnel from a grouping's roster.

    Requires admin or super_admin role. Only allowed when grouping is in
    draft status. Idempotent — excluding an already-excluded personnel
    returns 200 with no change.
    """
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can manage exclusions",
        )

    # Verify grouping exists and is draft
    result = await db.execute(select(Grouping).where(Grouping.id == grouping_id))
    grouping = result.scalar_one_or_none()
    if not grouping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Grouping not found: {grouping_id}",
        )
    if grouping.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Exclusions can only be modified for draft groupings "
                f"(current status: '{grouping.status}')."
            ),
        )

    # Verify personnel belongs to this grouping's nominal roll
    personnel_result = await db.execute(
        select(Personnel).where(
            Personnel.id == exclusion_data.personnel_id,
            Personnel.nominal_roll_id == grouping.nominal_roll_id,
        )
    )
    if not personnel_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personnel not found in this grouping's nominal roll.",
        )

    # Check if already excluded (idempotent)
    existing = await db.execute(
        select(GroupingPersonnelExclusion).where(
            GroupingPersonnelExclusion.grouping_id == grouping_id,
            GroupingPersonnelExclusion.personnel_id == exclusion_data.personnel_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"detail": "Personnel already excluded"}

    exclusion = GroupingPersonnelExclusion(
        grouping_id=grouping_id,
        personnel_id=exclusion_data.personnel_id,
        excluded_by=user_id,
    )
    db.add(exclusion)
    await db.commit()

    return {"detail": "Personnel excluded"}


@router.delete(
    "/{grouping_id}/exclusions/{personnel_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_exclusion(
    grouping_id: str,
    personnel_id: str,
    user_id: str = Query(..., description="User ID removing the exclusion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Re-include a previously excluded personnel in a grouping's roster.

    Requires admin or super_admin role. Only allowed when grouping is in
    draft status.
    """
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can manage exclusions",
        )

    # Verify grouping exists and is draft
    result = await db.execute(select(Grouping).where(Grouping.id == grouping_id))
    grouping = result.scalar_one_or_none()
    if not grouping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Grouping not found: {grouping_id}",
        )
    if grouping.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Exclusions can only be modified for draft groupings "
                f"(current status: '{grouping.status}')."
            ),
        )

    # Find and delete the exclusion
    result = await db.execute(
        select(GroupingPersonnelExclusion).where(
            GroupingPersonnelExclusion.grouping_id == grouping_id,
            GroupingPersonnelExclusion.personnel_id == personnel_id,
        )
    )
    exclusion = result.scalar_one_or_none()
    if not exclusion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Personnel is not excluded from this grouping.",
        )

    await db.delete(exclusion)
    await db.commit()

    return {"detail": "Personnel re-included"}


# ============================================================================
# Grouping Personnel Overrides
# ============================================================================


@router.post(
    "/{grouping_id}/personnel-overrides",
    response_model=GroupingPersonnelOverrideResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_personnel_override(
    grouping_id: str,
    override_data: GroupingPersonnelOverrideCreate,
    user_id: str = Query(..., description="User ID creating the override"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a grouping personnel override.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can create personnel overrides",
        )

    # Verify grouping exists
    await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Check if override already exists
    existing_result = await db.execute(
        select(GroupingPersonnelOverride).where(
            and_(
                GroupingPersonnelOverride.grouping_id == grouping_id,
                GroupingPersonnelOverride.personnel_id == override_data.personnel_id,
            )
        )
    )
    existing_override = existing_result.scalar_one_or_none()

    if existing_override:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personnel override already exists for this grouping and personnel",
        )

    # Create override
    override = GroupingPersonnelOverride(
        grouping_id=grouping_id,
        personnel_id=override_data.personnel_id,
        unit=override_data.unit,
        sub_unit_1=override_data.sub_unit_1,
        sub_unit_2=override_data.sub_unit_2,
        sub_unit_3=override_data.sub_unit_3,
        checkbox=override_data.checkbox,
        remarks=override_data.remarks,
        created_by=user_id,
        updated_at=utc_dt.utcnow(),
    )

    db.add(override)
    await db.commit()
    await db.refresh(override)

    return override


@router.get(
    "/{grouping_id}/personnel-overrides",
    response_model=list[GroupingPersonnelOverrideResponse],
)
async def list_personnel_overrides(
    grouping_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """List all personnel overrides for a grouping.

    Requires appropriate access permissions.
    """
    # Verify grouping exists and user has access
    await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Get overrides
    result = await db.execute(
        select(GroupingPersonnelOverride).where(
            GroupingPersonnelOverride.grouping_id == grouping_id
        )
    )
    overrides = result.scalars().all()

    return overrides


# ============================================================================
# Grouping Notes
# ============================================================================


@router.post(
    "/{grouping_id}/notes",
    response_model=GroupingNotesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_grouping_notes(
    grouping_id: str,
    notes_data: GroupingNotesCreate,
    user_id: str = Query(..., description="User ID creating the notes"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create grouping notes for a personnel.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can create grouping notes",
        )

    # Verify grouping exists
    await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Check if notes already exist
    existing_result = await db.execute(
        select(GroupingNotes).where(
            and_(
                GroupingNotes.grouping_id == grouping_id,
                GroupingNotes.personnel_id == notes_data.personnel_id,
            )
        )
    )
    existing_notes = existing_result.scalar_one_or_none()

    if existing_notes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Grouping notes already exist for this personnel. Use update endpoint.",
        )

    # Create notes
    notes = GroupingNotes(
        grouping_id=grouping_id,
        personnel_id=notes_data.personnel_id,
        notes=notes_data.notes,
        created_by=user_id,
        updated_by=user_id,
        notes_version=1,
    )

    db.add(notes)
    await db.commit()
    await db.refresh(notes)

    return notes


@router.get("/{grouping_id}/notes", response_model=list[GroupingNotesResponse])
async def list_grouping_notes(
    grouping_id: str,
    personnel_id: str | None = None,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """List grouping notes.

    Requires appropriate access permissions.
    """
    # Verify grouping exists and user has access
    await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Build query
    query = select(GroupingNotes).where(
        GroupingNotes.grouping_id == grouping_id
    )

    if personnel_id:
        query = query.where(GroupingNotes.personnel_id == personnel_id)

    result = await db.execute(query)
    notes_list = result.scalars().all()

    return notes_list


@router.patch(
    "/{grouping_id}/notes/{personnel_id}", response_model=GroupingNotesResponse
)
async def update_grouping_notes(
    grouping_id: str,
    personnel_id: str,
    notes_data: GroupingNotesUpdate,
    user_id: str = Query(..., description="User ID updating the notes"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update grouping notes for a personnel.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can update grouping notes",
        )

    # Verify grouping exists
    await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Get existing notes
    result = await db.execute(
        select(GroupingNotes).where(
            and_(
                GroupingNotes.grouping_id == grouping_id,
                GroupingNotes.personnel_id == personnel_id,
            )
        )
    )
    notes = result.scalar_one_or_none()

    if not notes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grouping notes not found for this personnel",
        )

    # Update notes
    notes.notes = notes_data.notes
    notes.updated_by = user_id
    notes.updated_at = utc_dt.utcnow()
    notes.notes_version += 1

    await db.commit()
    await db.refresh(notes)

    return notes


# ============================================================================
# Grouping Status
# ============================================================================


@router.get("/{grouping_id}/status", response_model=GroupingStatusResponse)
async def get_grouping_status(
    grouping_id: str,
    status_date: utc_dt.date | None = Query(
        None, description="Date to get status for (defaults to today)"
    ),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get grouping status for a specific date.

    Returns current snapshot including:
    - Grouping info
    - Today's AM/PM attendance status
    - Personnel counts by attendance status
    - Unit-level breakdown

    Defaults to today if no date provided.
    """
    # Verify grouping exists and user has access
    grouping = await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Default to today if no date provided
    if status_date is None:
        status_date = utc_dt.utcnow().date()

    # Fetch attendance rows for the grouping's NR on the date.
    attendance_result = await db.execute(
        select(Attendance).where(
            and_(
                Attendance.nominal_roll_id == grouping.nominal_roll_id,
                Attendance.date == status_date,
            )
        )
    )
    rows = list(attendance_result.scalars().all())

    # Build AM/PM present/absent/total counts.
    def _slot_stats(slot: str) -> GroupingStatusSessionInfo | None:
        if not rows:
            return None
        present = 0
        total = 0
        for row in rows:
            value = row.status_am if slot == "am" else row.status_pm
            total += 1
            if value in PRESENT_LIKE_STATUSES:
                present += 1
        return GroupingStatusSessionInfo(
            status="open",  # AM/PM are hardcoded; "open" keeps the schema happy
            present=present,
            absent=total - present,
            total=total,
        )

    am_session_info = _slot_stats("am")
    pm_session_info = _slot_stats("pm")

    # Unit-level breakdown — aggregate both slots per unit.
    unit_stats: dict[str, dict[str, int]] = {}
    for row in rows:
        unit_name = row.unit_snapshot or "—"
        stats = unit_stats.setdefault(unit_name, {"total": 0, "present": 0})
        for value in (row.status_am, row.status_pm):
            stats["total"] += 1
            if value in PRESENT_LIKE_STATUSES:
                stats["present"] += 1

    units = [
        GroupingStatusUnitBreakdown(
            name=unit_name,
            total=stats["total"],
            present=stats["present"],
            absent=stats["total"] - stats["present"],
        )
        for unit_name, stats in sorted(unit_stats.items())
    ]

    return GroupingStatusResponse(
        grouping_id=grouping.id,
        grouping_name=grouping.name,
        date=status_date,
        grouping_status=grouping.status,  # type: ignore[assignment]
        am_session=am_session_info,
        pm_session=pm_session_info,
        units=units,
    )


# ============================================================================
# CSV Export
# ============================================================================


@router.get("/{grouping_id}/export")
async def export_grouping_csv(
    grouping_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Export grouping data to CSV format for debugging and analysis.

    Returns a CSV file containing:
    - Personnel records with grouping-specific assignments
    - Attendance rows (AM/PM status + remarks per date)
    - Grouping notes

    Access-controlled by grouping scope.
    """
    # Verify grouping exists and user has access
    grouping = await verify_grouping_access(grouping_id, user_id, user_role, db)

    # Get all personnel records for this grouping from the nominal roll
    personnel_result = await db.execute(
        select(Personnel).where(
            Personnel.nominal_roll_id == grouping.nominal_roll_id
        )
    )
    all_personnel = personnel_result.scalars().all()

    # Get grouping overrides
    overrides_result = await db.execute(
        select(GroupingPersonnelOverride).where(
            GroupingPersonnelOverride.grouping_id == grouping_id
        )
    )
    overrides = overrides_result.scalars().all()

    # Create a mapping of personnel_id to override
    override_map = {override.personnel_id: override for override in overrides}

    # Get grouping notes
    notes_result = await db.execute(
        select(GroupingNotes).where(GroupingNotes.grouping_id == grouping_id)
    )
    notes = notes_result.scalars().all()

    # Create a mapping of personnel_id to notes
    notes_map = {note.personnel_id: note.notes for note in notes}

    # Get attendance rows for this grouping's NR.
    attendance_result = await db.execute(
        select(Attendance).where(
            Attendance.nominal_roll_id == grouping.nominal_roll_id
        )
    )
    attendance_records = attendance_result.scalars().all()

    # Distinct dates (sorted ascending) for column headers.
    dates = sorted({record.date for record in attendance_records})

    # Map (personnel_id, date) -> attendance row.
    attendance_map = {
        (record.personnel_id, record.date): record
        for record in attendance_records
    }

    # Generate CSV data
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(
        [
            "ID",
            "Rank",
            "Name",
            "Nominal Roll Unit",
            "Nominal Roll SubUnit 1",
            "Nominal Roll SubUnit 2",
            "Nominal Roll SubUnit 3",
            "Override Unit",
            "Override SubUnit 1",
            "Override SubUnit 2",
            "Override SubUnit 3",
            "Checkbox",
            "Remarks",
            "Grouping Notes",
        ]
        + [
            f"{d.strftime('%Y-%m-%d')} AM Status"
            for d in dates
        ]
        + [
            f"{d.strftime('%Y-%m-%d')} AM Remarks"
            for d in dates
        ]
        + [
            f"{d.strftime('%Y-%m-%d')} PM Status"
            for d in dates
        ]
        + [
            f"{d.strftime('%Y-%m-%d')} PM Remarks"
            for d in dates
        ]
    )

    # Write personnel rows
    for person in all_personnel:
        # Get override if exists
        override = override_map.get(person.id)
        person_notes = notes_map.get(person.id, "")

        # Build row
        row = [
            person.short_id,
            person.rank,
            person.full_name,
            person.unit,
            person.sub_unit_1 or "",
            person.sub_unit_2 or "",
            person.sub_unit_3 or "",
            override.unit if override else "",
            override.sub_unit_1 if override else "",
            override.sub_unit_2 if override else "",
            override.sub_unit_3 if override else "",
            "Yes" if override and override.checkbox else "",
            override.remarks if override else "",
            person_notes,
        ]

        # Add attendance data for each date (AM status/remarks, then PM).
        for d in dates:
            record = attendance_map.get((person.id, d))
            row.append(record.status_am if record else "")
            row.append(record.remarks_am or "" if record else "")
        for d in dates:
            record = attendance_map.get((person.id, d))
            row.append(record.status_pm if record else "")
            row.append(record.remarks_pm or "" if record else "")

        writer.writerow(row)

    # Prepare response
    csv_data = output.getvalue()
    output.close()

    # Create filename with grouping name and timestamp
    filename = f"grouping_{grouping.name.replace(' ', '_')}_{utc_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        io.BytesIO(csv_data.encode("utf-8")),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )
