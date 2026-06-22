"""Deployment management API endpoints."""

import csv

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models.attendance import AttendanceRecord, Session
from parade_state.models.deployment import (
    Deployment,
    DeploymentNotes,
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

    # Validate date range
    if deployment_data.valid_until <= deployment_data.valid_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="valid_until must be after valid_from",
        )

    # Create deployment
    deployment = Deployment(
        name=deployment_data.name,
        estab_id=deployment_data.estab_id,
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
    estab_id: str | None = None,
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

    if estab_id:
        query = query.where(Deployment.estab_id == estab_id)

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
    status_date: date | None = Query(
        None, description="Date to get status for (defaults to today)"
    ),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get deployment status for a specific date.

    Returns current snapshot including:
    - Deployment info
    - Today's AM/PM session status
    - Personnel counts by attendance status
    - Unit-level breakdown

    Defaults to today if no date provided.
    """
    # Verify deployment exists and user has access
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Default to today if no date provided
    if status_date is None:
        status_date = utc_dt.utcnow().date()

    # Get sessions for the date
    sessions_result = await db.execute(
        select(Session).where(
            and_(
                Session.deployment_id == deployment_id,
                Session.date == status_date,
            )
        )
    )
    sessions = sessions_result.scalars().all()

    # Initialize session info
    am_session_info = None
    pm_session_info = None

    # Process sessions
    for session in sessions:
        # Get attendance records for this session
        attendance_result = await db.execute(
            select(
                AttendanceRecord.status,
                func.count(AttendanceRecord.id).label("count"),
            )
            .where(
                and_(
                    AttendanceRecord.session_id == session.id,
                    AttendanceRecord.deployment_id == deployment_id,
                )
            )
            .group_by(AttendanceRecord.status)
        )
        attendance_counts = attendance_result.all()

        # Build status counts
        counts = {"present": 0, "absent": 0, "excused": 0, "unknown": 0}
        total = 0
        for status_val, count in attendance_counts:
            counts[status_val] = count
            total += count

        # Create session info
        session_info = DeploymentStatusSessionInfo(
            status=session.status,  # type: ignore[assignment]
            present=counts["present"],
            absent=counts["absent"],
            excused=counts["excused"],
            unknown=counts["unknown"],
            total=total,
        )

        if session.session_type == "AM":
            am_session_info = session_info
        else:  # PM
            pm_session_info = session_info

    # Get unit-level breakdown
    # Get all attendance records for the date with unit snapshots
    unit_breakdown_result = await db.execute(
        select(
            AttendanceRecord.unit_snapshot,
            AttendanceRecord.status,
            func.count(AttendanceRecord.id).label("count"),
        )
        .where(
            and_(
                AttendanceRecord.deployment_id == deployment_id,
                AttendanceRecord.session_id.in_([s.id for s in sessions]),
            )
        )
        .group_by(AttendanceRecord.unit_snapshot, AttendanceRecord.status)
    )
    unit_records = unit_breakdown_result.all()

    # Aggregate by unit
    unit_stats = {}
    for unit_name, status_val, count in unit_records:
        if unit_name not in unit_stats:
            unit_stats[unit_name] = {
                "total": 0,
                "present": 0,
                "absent": 0,
                "excused": 0,
                "unknown": 0,
            }
        unit_stats[unit_name][status_val] = count
        unit_stats[unit_name]["total"] += count

    # Create unit breakdown list
    units = [
        DeploymentStatusUnitBreakdown(
            name=unit_name,
            total=stats["total"],
            present=stats["present"],
            absent=stats["absent"],
            excused=stats["excused"],
            unknown=stats["unknown"],
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
    - Attendance records
    - Session information
    - Deployment notes

    Access-controlled by deployment scope.
    """
    # Verify deployment exists and user has access
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Get all personnel records for this deployment from the estab
    personnel_result = await db.execute(
        select(Personnel).where(Personnel.estab_id == deployment.estab_id)
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

    # Get sessions for this deployment
    sessions_result = await db.execute(
        select(Session).where(Session.deployment_id == deployment_id)
    )
    sessions = sessions_result.scalars().all()

    # Get attendance records
    attendance_result = await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.deployment_id == deployment_id)
    )
    attendance_records = attendance_result.scalars().all()

    # Create a mapping of (session_id, personnel_id) to attendance record
    attendance_map = {
        (record.session_id, record.personnel_id): record
        for record in attendance_records
    }

    # Generate CSV data
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(
        [
            "Service Number",
            "Rank",
            "Name",
            "Estab Unit",
            "Estab SubUnit 1",
            "Estab SubUnit 2",
            "Estab SubUnit 3",
            "Override Unit",
            "Override SubUnit 1",
            "Override SubUnit 2",
            "Override SubUnit 3",
            "Deployment Notes",
        ]
        + [
            f"Session {s.date.strftime('%Y-%m-%d')} {s.session_type} Status"
            for s in sessions
        ]
        + [
            f"Session {s.date.strftime('%Y-%m-%d')} {s.session_type} Remarks"
            for s in sessions
        ]
    )

    # Write personnel rows
    for person in all_personnel:
        # Get override if exists
        override = override_map.get(person.id)
        person_notes = notes_map.get(person.id, "")

        # Build row
        row = [
            person.pers_no,
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

        # Add attendance data for each session
        for session in sessions:
            attendance = attendance_map.get((session.id, person.id))
            if attendance:
                row.append(attendance.status)
                row.append(attendance.remarks or "")
            else:
                row.append("")  # No status
                row.append("")  # No remarks

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
