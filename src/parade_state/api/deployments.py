"""Deployment management API endpoints."""

import csv

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models.attendance import PRESENT_LIKE_STATUSES, Attendance
from parade_state.models.csv_ingestion import NominalRoll
from parade_state.models.deployment import (
    Deployment,
    DeploymentNotes,
    DeploymentPersonnelExclusion,
    DeploymentPersonnelOverride,
)
from parade_state.models.personnel import Personnel
from parade_state.models.schemas import (
    DeploymentCreate,
    DeploymentNotesCreate,
    DeploymentNotesResponse,
    DeploymentNotesUpdate,
    DeploymentPersonnelOverrideCreate,
    DeploymentPersonnelOverrideResponse,
    DeploymentResponse,
    DeploymentStatusResponse,
    DeploymentStatusSessionInfo,
    DeploymentStatusUnitBreakdown,
    DeploymentUpdate,
    ExclusionCreate,
)
from parade_state.utils import utc_dt

router = APIRouter()


# ============================================================================
# Helper Functions
# ============================================================================


async def verify_deployment_access(
    deployment_id: str,
    user_id: str,
    user_role: str,
    db: AsyncSession,
) -> Deployment:
    """Verify user has access to deployment and return it."""
    # Super admins have full access
    if user_role == "super_admin":
        result = await db.execute(
            select(Deployment).where(Deployment.id == deployment_id)
        )
        deployment = result.scalar_one_or_none()
        if not deployment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Deployment not found",
            )
        return deployment

    # For regular users and admins, check deployment access
    # TODO: Implement proper access control based on user scopes
    # For now, admins can access all deployments
    if user_role in ["admin", "user"]:
        result = await db.execute(
            select(Deployment).where(Deployment.id == deployment_id)
        )
        deployment = result.scalar_one_or_none()
        if not deployment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Deployment not found",
            )
        return deployment

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions to access this deployment",
    )


async def validate_deployment_status_transition(
    current_status: str,
    new_status: str,
) -> bool:
    """Validate deployment status transition is allowed."""
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
# Deployment CRUD Endpoints
# ============================================================================


@router.post(
    "/", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED
)
async def create_deployment(
    deployment_data: DeploymentCreate,
    user_id: str = Query(..., description="User ID creating the deployment"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new deployment.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can create deployments",
        )

    # Verify nominal roll exists and is confirmed
    result = await db.execute(
        select(NominalRoll).where(NominalRoll.id == deployment_data.nominal_roll_id)
    )
    nominal_roll = result.scalar_one_or_none()
    if not nominal_roll:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nominal roll {deployment_data.nominal_roll_id} not found",
        )
    if nominal_roll.status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot create deployment from nominal roll in '{nominal_roll.status}' status. "
                "Nominal roll must be confirmed."
            ),
        )

    # Validate date range
    if deployment_data.valid_until <= deployment_data.valid_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="valid_until must be after valid_from",
        )

    # Create deployment
    deployment = Deployment(
        name=deployment_data.name,
        nominal_roll_id=deployment_data.nominal_roll_id,
        status=deployment_data.status,
        valid_from=deployment_data.valid_from,
        valid_until=deployment_data.valid_until,
        scheduled_activation=deployment_data.scheduled_activation,
        notes=deployment_data.notes,
        created_by=user_id,
    )

    # Auto-activate if status is active
    if deployment_data.status == "active":
        deployment.activated_at = utc_dt.utcnow()

    db.add(deployment)
    await db.commit()
    await db.refresh(deployment)

    return deployment


@router.get("/", response_model=list[DeploymentResponse])
async def list_deployments(
    status: str | None = None,
    nominal_roll_id: str | None = None,
    search: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for filtering"),
    db: AsyncSession = Depends(get_db_session),
):
    """List deployments with optional filtering.

    All authenticated users can list deployments.
    Filters may be applied based on user role.
    """
    query = select(Deployment)

    # Apply filters
    if status:
        query = query.where(Deployment.status == status)

    if nominal_roll_id:
        query = query.where(Deployment.nominal_roll_id == nominal_roll_id)

    if search:
        search_pattern = f"%{search}%"
        query = query.where(Deployment.name.ilike(search_pattern))

    # Order by created_at descending
    query = query.order_by(Deployment.created_at.desc())

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    deployments = result.scalars().all()

    return deployments


@router.get("/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(
    deployment_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a specific deployment by ID.

    Requires appropriate access permissions.
    """
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)
    return deployment


@router.patch("/{deployment_id}", response_model=DeploymentResponse)
async def update_deployment(
    deployment_id: str,
    update_data: DeploymentUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update a deployment.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can update deployments",
        )

    # Get deployment
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Validate date range if both provided
    if update_data.valid_from and update_data.valid_until:
        if update_data.valid_until <= update_data.valid_from:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="valid_until must be after valid_from",
            )

    # Update fields
    if update_data.name is not None:
        deployment.name = update_data.name

    if update_data.valid_from is not None:
        deployment.valid_from = update_data.valid_from

    if update_data.valid_until is not None:
        deployment.valid_until = update_data.valid_until

    if update_data.scheduled_activation is not None:
        deployment.scheduled_activation = update_data.scheduled_activation

    if update_data.notes is not None:
        deployment.notes = update_data.notes

    # Handle status transition
    if update_data.status is not None:
        current_status = deployment.status
        new_status = update_data.status

        # Validate transition
        if not await validate_deployment_status_transition(current_status, new_status):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status transition from {current_status} to {new_status}",
            )

        # Handle activation
        if new_status == "active" and current_status != "active":
            # Check if another deployment is already active
            active_result = await db.execute(
                select(Deployment).where(
                    and_(
                        Deployment.status == "active",
                        Deployment.id != deployment_id,
                    )
                )
            )
            active_deployment = active_result.scalar_one_or_none()

            if active_deployment:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Another deployment is already active. Only one deployment can be active at a time.",
                )

            deployment.activated_at = utc_dt.utcnow()

        # Handle deactivation
        if new_status in ["inactive", "closed"] and current_status == "active":
            deployment.deactivated_at = utc_dt.utcnow()

        deployment.status = new_status

    await db.commit()
    await db.refresh(deployment)

    return deployment


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(
    deployment_id: str,
    user_id: str = Query(..., description="User ID making the deletion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a deployment.

    Requires super_admin role.
    """
    # Verify user has permission
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can delete deployments",
        )

    # Get deployment
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Prevent deletion of active or finalized deployments
    if deployment.status in ["active", "finalized"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete deployment with status {deployment.status}",
        )

    await db.delete(deployment)
    await db.commit()

    return None


# ============================================================================
# Deployment Activation Endpoints
# ============================================================================


@router.post("/{deployment_id}/activate", response_model=DeploymentResponse)
async def activate_deployment(
    deployment_id: str,
    user_id: str = Query(..., description="User ID activating the deployment"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Manually activate a deployment.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can activate deployments",
        )

    # Get deployment
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Check if already active
    if deployment.status == "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deployment is already active",
        )

    # Check if another deployment is already active
    active_result = await db.execute(
        select(Deployment).where(
            and_(
                Deployment.status == "active",
                Deployment.id != deployment_id,
            )
        )
    )
    active_deployment = active_result.scalar_one_or_none()

    if active_deployment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Another deployment is already active. Only one deployment can be active at a time.",
        )

    # Validate transition
    if not await validate_deployment_status_transition(deployment.status, "active"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot activate deployment with status {deployment.status}",
        )

    # Activate deployment
    deployment.status = "active"
    deployment.activated_at = utc_dt.utcnow()

    await db.commit()
    await db.refresh(deployment)

    return deployment


@router.post("/{deployment_id}/deactivate", response_model=DeploymentResponse)
async def deactivate_deployment(
    deployment_id: str,
    user_id: str = Query(..., description="User ID deactivating the deployment"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Manually deactivate a deployment.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can deactivate deployments",
        )

    # Get deployment
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Check if currently active
    if deployment.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active deployments can be deactivated",
        )

    # Deactivate deployment
    deployment.status = "inactive"
    deployment.deactivated_at = utc_dt.utcnow()

    await db.commit()
    await db.refresh(deployment)

    return deployment


# ============================================================================
# Deployment Personnel Exclusions
# ============================================================================


@router.post(
    "/{deployment_id}/exclusions",
    status_code=status.HTTP_201_CREATED,
)
async def create_exclusion(
    deployment_id: str,
    exclusion_data: ExclusionCreate,
    user_id: str = Query(..., description="User ID creating the exclusion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Exclude a personnel from a deployment's roster.

    Requires admin or super_admin role. Only allowed when deployment is in
    draft status. Idempotent — excluding an already-excluded personnel
    returns 200 with no change.
    """
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can manage exclusions",
        )

    # Verify deployment exists and is draft
    result = await db.execute(select(Deployment).where(Deployment.id == deployment_id))
    deployment = result.scalar_one_or_none()
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )
    if deployment.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Exclusions can only be modified for draft deployments "
                f"(current status: '{deployment.status}')."
            ),
        )

    # Verify personnel belongs to this deployment's nominal roll
    personnel_result = await db.execute(
        select(Personnel).where(
            Personnel.id == exclusion_data.personnel_id,
            Personnel.nominal_roll_id == deployment.nominal_roll_id,
        )
    )
    if not personnel_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personnel not found in this deployment's nominal roll.",
        )

    # Check if already excluded (idempotent)
    existing = await db.execute(
        select(DeploymentPersonnelExclusion).where(
            DeploymentPersonnelExclusion.deployment_id == deployment_id,
            DeploymentPersonnelExclusion.personnel_id == exclusion_data.personnel_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"detail": "Personnel already excluded"}

    exclusion = DeploymentPersonnelExclusion(
        deployment_id=deployment_id,
        personnel_id=exclusion_data.personnel_id,
        excluded_by=user_id,
    )
    db.add(exclusion)
    await db.commit()

    return {"detail": "Personnel excluded"}


@router.delete(
    "/{deployment_id}/exclusions/{personnel_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_exclusion(
    deployment_id: str,
    personnel_id: str,
    user_id: str = Query(..., description="User ID removing the exclusion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Re-include a previously excluded personnel in a deployment's roster.

    Requires admin or super_admin role. Only allowed when deployment is in
    draft status.
    """
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can manage exclusions",
        )

    # Verify deployment exists and is draft
    result = await db.execute(select(Deployment).where(Deployment.id == deployment_id))
    deployment = result.scalar_one_or_none()
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )
    if deployment.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Exclusions can only be modified for draft deployments "
                f"(current status: '{deployment.status}')."
            ),
        )

    # Find and delete the exclusion
    result = await db.execute(
        select(DeploymentPersonnelExclusion).where(
            DeploymentPersonnelExclusion.deployment_id == deployment_id,
            DeploymentPersonnelExclusion.personnel_id == personnel_id,
        )
    )
    exclusion = result.scalar_one_or_none()
    if not exclusion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Personnel is not excluded from this deployment.",
        )

    await db.delete(exclusion)
    await db.commit()

    return {"detail": "Personnel re-included"}


# ============================================================================
# Deployment Personnel Overrides
# ============================================================================


@router.post(
    "/{deployment_id}/personnel-overrides",
    response_model=DeploymentPersonnelOverrideResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_personnel_override(
    deployment_id: str,
    override_data: DeploymentPersonnelOverrideCreate,
    user_id: str = Query(..., description="User ID creating the override"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a deployment personnel override.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can create personnel overrides",
        )

    # Verify deployment exists
    await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Check if override already exists
    existing_result = await db.execute(
        select(DeploymentPersonnelOverride).where(
            and_(
                DeploymentPersonnelOverride.deployment_id == deployment_id,
                DeploymentPersonnelOverride.personnel_id == override_data.personnel_id,
            )
        )
    )
    existing_override = existing_result.scalar_one_or_none()

    if existing_override:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personnel override already exists for this deployment and personnel",
        )

    # Create override
    override = DeploymentPersonnelOverride(
        deployment_id=deployment_id,
        personnel_id=override_data.personnel_id,
        unit=override_data.unit,
        sub_unit_1=override_data.sub_unit_1,
        sub_unit_2=override_data.sub_unit_2,
        sub_unit_3=override_data.sub_unit_3,
        created_by=user_id,
        updated_at=utc_dt.utcnow(),
    )

    db.add(override)
    await db.commit()
    await db.refresh(override)

    return override


@router.get(
    "/{deployment_id}/personnel-overrides",
    response_model=list[DeploymentPersonnelOverrideResponse],
)
async def list_personnel_overrides(
    deployment_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """List all personnel overrides for a deployment.

    Requires appropriate access permissions.
    """
    # Verify deployment exists and user has access
    await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Get overrides
    result = await db.execute(
        select(DeploymentPersonnelOverride).where(
            DeploymentPersonnelOverride.deployment_id == deployment_id
        )
    )
    overrides = result.scalars().all()

    return overrides


# ============================================================================
# Deployment Notes
# ============================================================================


@router.post(
    "/{deployment_id}/notes",
    response_model=DeploymentNotesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_deployment_notes(
    deployment_id: str,
    notes_data: DeploymentNotesCreate,
    user_id: str = Query(..., description="User ID creating the notes"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create deployment notes for a personnel.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can create deployment notes",
        )

    # Verify deployment exists
    await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Check if notes already exist
    existing_result = await db.execute(
        select(DeploymentNotes).where(
            and_(
                DeploymentNotes.deployment_id == deployment_id,
                DeploymentNotes.personnel_id == notes_data.personnel_id,
            )
        )
    )
    existing_notes = existing_result.scalar_one_or_none()

    if existing_notes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deployment notes already exist for this personnel. Use update endpoint.",
        )

    # Create notes
    notes = DeploymentNotes(
        deployment_id=deployment_id,
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


@router.get("/{deployment_id}/notes", response_model=list[DeploymentNotesResponse])
async def list_deployment_notes(
    deployment_id: str,
    personnel_id: str | None = None,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """List deployment notes.

    Requires appropriate access permissions.
    """
    # Verify deployment exists and user has access
    await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Build query
    query = select(DeploymentNotes).where(
        DeploymentNotes.deployment_id == deployment_id
    )

    if personnel_id:
        query = query.where(DeploymentNotes.personnel_id == personnel_id)

    result = await db.execute(query)
    notes_list = result.scalars().all()

    return notes_list


@router.patch(
    "/{deployment_id}/notes/{personnel_id}", response_model=DeploymentNotesResponse
)
async def update_deployment_notes(
    deployment_id: str,
    personnel_id: str,
    notes_data: DeploymentNotesUpdate,
    user_id: str = Query(..., description="User ID updating the notes"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update deployment notes for a personnel.

    Requires admin or super_admin role.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can update deployment notes",
        )

    # Verify deployment exists
    await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Get existing notes
    result = await db.execute(
        select(DeploymentNotes).where(
            and_(
                DeploymentNotes.deployment_id == deployment_id,
                DeploymentNotes.personnel_id == personnel_id,
            )
        )
    )
    notes = result.scalar_one_or_none()

    if not notes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment notes not found for this personnel",
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
# Deployment Status
# ============================================================================


@router.get("/{deployment_id}/status", response_model=DeploymentStatusResponse)
async def get_deployment_status(
    deployment_id: str,
    status_date: utc_dt.date | None = Query(
        None, description="Date to get status for (defaults to today)"
    ),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get deployment status for a specific date.

    Returns current snapshot including:
    - Deployment info
    - Today's AM/PM attendance status
    - Personnel counts by attendance status
    - Unit-level breakdown

    Defaults to today if no date provided.
    """
    # Verify deployment exists and user has access
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Default to today if no date provided
    if status_date is None:
        status_date = utc_dt.utcnow().date()

    # Fetch attendance rows for the deployment's NR on the date.
    attendance_result = await db.execute(
        select(Attendance).where(
            and_(
                Attendance.nominal_roll_id == deployment.nominal_roll_id,
                Attendance.date == status_date,
            )
        )
    )
    rows = list(attendance_result.scalars().all())

    # Build AM/PM present/absent/total counts.
    def _slot_stats(slot: str) -> DeploymentStatusSessionInfo | None:
        if not rows:
            return None
        present = 0
        total = 0
        for row in rows:
            value = row.status_am if slot == "am" else row.status_pm
            total += 1
            if value in PRESENT_LIKE_STATUSES:
                present += 1
        return DeploymentStatusSessionInfo(
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
        DeploymentStatusUnitBreakdown(
            name=unit_name,
            total=stats["total"],
            present=stats["present"],
            absent=stats["total"] - stats["present"],
        )
        for unit_name, stats in sorted(unit_stats.items())
    ]

    return DeploymentStatusResponse(
        deployment_id=deployment.id,
        deployment_name=deployment.name,
        date=status_date,
        deployment_status=deployment.status,  # type: ignore[assignment]
        am_session=am_session_info,
        pm_session=pm_session_info,
        units=units,
    )


# ============================================================================
# CSV Export
# ============================================================================


@router.get("/{deployment_id}/export")
async def export_deployment_csv(
    deployment_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Export deployment data to CSV format for debugging and analysis.

    Returns a CSV file containing:
    - Personnel records with deployment-specific assignments
    - Attendance rows (AM/PM status + remarks per date)
    - Deployment notes

    Access-controlled by deployment scope.
    """
    # Verify deployment exists and user has access
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Get all personnel records for this deployment from the nominal roll
    personnel_result = await db.execute(
        select(Personnel).where(
            Personnel.nominal_roll_id == deployment.nominal_roll_id
        )
    )
    all_personnel = personnel_result.scalars().all()

    # Get deployment overrides
    overrides_result = await db.execute(
        select(DeploymentPersonnelOverride).where(
            DeploymentPersonnelOverride.deployment_id == deployment_id
        )
    )
    overrides = overrides_result.scalars().all()

    # Create a mapping of personnel_id to override
    override_map = {override.personnel_id: override for override in overrides}

    # Get deployment notes
    notes_result = await db.execute(
        select(DeploymentNotes).where(DeploymentNotes.deployment_id == deployment_id)
    )
    notes = notes_result.scalars().all()

    # Create a mapping of personnel_id to notes
    notes_map = {note.personnel_id: note.notes for note in notes}

    # Get attendance rows for this deployment's NR.
    attendance_result = await db.execute(
        select(Attendance).where(
            Attendance.nominal_roll_id == deployment.nominal_roll_id
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
            "Deployment Notes",
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

    # Create filename with deployment name and timestamp
    filename = f"deployment_{deployment.name.replace(' ', '_')}_{utc_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        io.BytesIO(csv_data.encode("utf-8")),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )
