"""Personnel management API endpoints."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parade_state.db import get_db_session
from parade_state.models import User, Personnel, Deployment, DeploymentPersonnelOverride, DeploymentNotes, AttendanceRecord, Session, DeploymentUserAccess
from parade_state.models.schemas import (
    PersonnelResponseWithDeployment,
    PersonnelUpdate,
    PersonnelListParams,
    PersonnelAttendanceHistoryResponse,
    PersonnelAttendanceHistoryItem,
    PersonnelAttendanceHistoryStats,
)

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
    """Verify user has access to deployment and return it.

    Super admins have full access to all deployments.
    Admins need explicit deployment access.
    Regular users need explicit deployment access.
    """
    # Get deployment
    result = await db.execute(select(Deployment).where(Deployment.id == deployment_id))
    deployment = result.scalar_one_or_none()

    if not deployment:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Deployment not found",
        )

    # Super admins have full access
    if user_role == "super_admin":
        return deployment

    # Check for explicit deployment access
    access_result = await db.execute(
        select(DeploymentUserAccess).where(
            and_(
                DeploymentUserAccess.user_id == user_id,
                DeploymentUserAccess.deployment_id == deployment_id,
                DeploymentUserAccess.revoked_at.is_(None),
            )
        )
    )
    access = access_result.scalar_one_or_none()

    # Both admins and regular users need explicit deployment access
    if access:
        return deployment

    # No access found
    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions to access this deployment",
    )


def apply_personnel_filters(query, params: PersonnelListParams):
    """Apply filters to personnel query.

    Handles deployment-specific filtering and search functionality.
    """
    # Filter by estab_id
    if params.estab_id:
        query = query.where(Personnel.estab_id == params.estab_id)

    # Filter by status
    if params.status:
        query = query.where(Personnel.status == params.status)

    # Filter by unit hierarchy
    if params.unit:
        query = query.where(Personnel.unit == params.unit)

    if params.sub_unit_1:
        query = query.where(Personnel.sub_unit_1 == params.sub_unit_1)

    if params.sub_unit_2:
        query = query.where(Personnel.sub_unit_2 == params.sub_unit_2)

    if params.sub_unit_3:
        query = query.where(Personnel.sub_unit_3 == params.sub_unit_3)

    # Search across name and service number
    if params.search:
        search_term = f"%{params.search}%"
        query = query.where(
            or_(
                Personnel.full_name.ilike(search_term),
                Personnel.pers_no.ilike(search_term),
            )
        )

    return query


async def get_deployment_personnel_with_overrides(
    deployment_id: str,
    params: PersonnelListParams,
    user_id: str,
    user_role: str,
    db: AsyncSession,
):
    """Get personnel list for a deployment with override-aware filtering.

    This is the core function for deployment-based personnel listing.
    It handles:
    - Deployment-specific personnel (from estab)
    - Personnel overrides for this deployment
    - Unit hierarchy filtering
    - Search functionality
    - Deployment notes integration
    """
    # Verify deployment exists and user has access
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Get base personnel query
    query = select(Personnel).where(Personnel.estab_id == deployment.estab_id)

    # Apply filters
    query = apply_personnel_filters(query, params)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_count_result = await db.execute(count_query)
    total_count = total_count_result.scalar() or 0

    # Apply pagination
    query = query.offset(params.offset).limit(params.limit)

    # Execute query
    result = await db.execute(query)
    personnel_list = result.scalars().all()

    # Get all personnel overrides for this deployment
    override_query = select(DeploymentPersonnelOverride).where(
        DeploymentPersonnelOverride.deployment_id == deployment_id
    )
    override_result = await db.execute(override_query)
    overrides = override_result.scalars().all()

    # Create override lookup dictionary
    override_dict = {override.personnel_id: override for override in overrides}

    # Get deployment notes for all personnel
    notes_query = select(DeploymentNotes).where(
        DeploymentNotes.deployment_id == deployment_id
    )
    notes_result = await db.execute(notes_query)
    notes = notes_result.scalars().all()

    # Create notes lookup dictionary
    notes_dict = {note.personnel_id: note.notes for note in notes}

    # Build response with deployment-specific information
    personnel_responses = []
    for personnel in personnel_list:
        override = override_dict.get(personnel.id)
        deployment_notes = notes_dict.get(personnel.id)

        # Determine effective unit assignment (overrides take precedence)
        effective_unit = override.unit if override else personnel.unit
        effective_sub_unit_1 = override.sub_unit_1 if override else personnel.sub_unit_1
        effective_sub_unit_2 = override.sub_unit_2 if override else personnel.sub_unit_2
        effective_sub_unit_3 = override.sub_unit_3 if override else personnel.sub_unit_3

        # Apply unit hierarchy filters to effective assignments
        if params.unit and effective_unit != params.unit:
            continue
        if params.sub_unit_1 and effective_sub_unit_1 != params.sub_unit_1:
            continue
        if params.sub_unit_2 and effective_sub_unit_2 != params.sub_unit_2:
            continue
        if params.sub_unit_3 and effective_sub_unit_3 != params.sub_unit_3:
            continue

        response = PersonnelResponseWithDeployment(
            id=personnel.id,
            estab_id=personnel.estab_id,
            service_number=personnel.pers_no,
            rank=personnel.rank,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            deployment_id=deployment_id,
            has_override=override is not None,
            deployment_notes=deployment_notes,
        )
        personnel_responses.append(response)

    return personnel_responses, total_count, deployment


async def get_personnel_by_id_with_deployment_context(
    personnel_id: str,
    deployment_id: str,
    user_id: str,
    user_role: str,
    db: AsyncSession,
) -> tuple[Personnel, DeploymentPersonnelOverride | None, DeploymentNotes | None]:
    """Get personnel by ID with deployment context.

    Returns personnel record, override (if any), and deployment notes (if any).
    """
    # Verify deployment exists and user has access
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Get personnel record
    result = await db.execute(select(Personnel).where(Personnel.id == personnel_id))
    personnel = result.scalar_one_or_none()

    if not personnel:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Personnel not found",
        )

    # Verify personnel belongs to deployment's estab
    if personnel.estab_id != deployment.estab_id:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Personnel does not belong to this deployment's establishment",
        )

    # Get override if exists
    override_result = await db.execute(
        select(DeploymentPersonnelOverride).where(
            and_(
                DeploymentPersonnelOverride.deployment_id == deployment_id,
                DeploymentPersonnelOverride.personnel_id == personnel_id,
            )
        )
    )
    override = override_result.scalar_one_or_none()

    # Get deployment notes if exists
    notes_result = await db.execute(
        select(DeploymentNotes).where(
            and_(
                DeploymentNotes.deployment_id == deployment_id,
                DeploymentNotes.personnel_id == personnel_id,
            )
        )
    )
    notes = notes_result.scalar_one_or_none()

    return personnel, override, notes


# ============================================================================
# Personnel Endpoints
# ============================================================================


@router.get("/personnel", response_model=list[PersonnelResponseWithDeployment])
async def list_personnel(
    deployment_id: str | None = Query(None, description="Filter by deployment ID"),
    estab_id: str | None = Query(None, description="Filter by establishment ID"),
    unit: str | None = Query(None, description="Filter by unit"),
    sub_unit_1: str | None = Query(None, description="Filter by sub-unit 1"),
    sub_unit_2: str | None = Query(None, description="Filter by sub-unit 2"),
    sub_unit_3: str | None = Query(None, description="Filter by sub-unit 3"),
    status: str | None = Query(None, description="Filter by personnel status"),
    search: str | None = Query(None, description="Search by name or service number"),
    limit: int = Query(100, ge=1, le=1000, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """List personnel with optional deployment context and filtering.

    When deployment_id is provided:
    - Returns personnel scoped to that deployment
    - Includes deployment-specific information (overrides, notes)
    - Respects deployment access control
    - Filters apply to effective unit assignments (overrides take precedence)

    When deployment_id is not provided:
    - Returns all personnel (admin/super_admin only)
    - No deployment-specific information included
    """
    params = PersonnelListParams(
        deployment_id=deployment_id,
        estab_id=estab_id,
        unit=unit,
        sub_unit_1=sub_unit_1,
        sub_unit_2=sub_unit_2,
        sub_unit_3=sub_unit_3,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )

    # Deployment-scoped query
    if params.deployment_id:
        personnel_list, total_count, deployment = await get_deployment_personnel_with_overrides(
            params.deployment_id, params, user_id, user_role, db
        )
        return personnel_list

    # Non-deployment query (admin/super_admin only)
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can list personnel without deployment context",
        )

    # Build query without deployment context
    query = select(Personnel)
    query = apply_personnel_filters(query, params)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_count_result = await db.execute(count_query)
    total_count = total_count_result.scalar() or 0

    # Apply pagination
    query = query.offset(params.offset).limit(params.limit)

    # Execute query
    result = await db.execute(query)
    personnel_list = result.scalars().all()

    # Build response without deployment context
    personnel_responses = [
        PersonnelResponseWithDeployment(
            id=p.id,
            estab_id=p.estab_id,
            service_number=p.pers_no,
            rank=p.rank,
            name=p.full_name,
            unit=p.unit,
            sub_unit_1=p.sub_unit_1,
            sub_unit_2=p.sub_unit_2,
            sub_unit_3=p.sub_unit_3,
            status=p.status,
            created_at=p.created_at,
            deployment_id=None,
            has_override=False,
            deployment_notes=None,
        )
        for p in personnel_list
    ]

    return personnel_responses


@router.get("/personnel/{personnel_id}", response_model=PersonnelResponseWithDeployment)
async def get_personnel(
    personnel_id: str,
    deployment_id: str | None = Query(None, description="Deployment ID for context"),
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get personnel by ID, optionally with deployment context.

    When deployment_id is provided, includes deployment-specific information
    such as overrides and deployment notes.
    """
    if deployment_id:
        # Get personnel with deployment context
        personnel, override, notes = await get_personnel_by_id_with_deployment_context(
            personnel_id, deployment_id, user_id, user_role, db
        )

        return PersonnelResponseWithDeployment(
            id=personnel.id,
            estab_id=personnel.estab_id,
            service_number=personnel.pers_no,
            rank=personnel.rank,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            deployment_id=deployment_id,
            has_override=override is not None,
            deployment_notes=notes.notes if notes else None,
        )
    else:
        # Get personnel without deployment context (admin/super_admin only)
        if user_role not in ["admin", "super_admin"]:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Only admins can view personnel without deployment context",
            )

        result = await db.execute(select(Personnel).where(Personnel.id == personnel_id))
        personnel = result.scalar_one_or_none()

        if not personnel:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Personnel not found",
            )

        return PersonnelResponseWithDeployment(
            id=personnel.id,
            estab_id=personnel.estab_id,
            service_number=personnel.pers_no,
            rank=personnel.rank,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            deployment_id=None,
            has_override=False,
            deployment_notes=None,
        )


@router.patch("/personnel/{personnel_id}", response_model=PersonnelResponseWithDeployment)
async def update_personnel(
    personnel_id: str,
    personnel_update: PersonnelUpdate,
    deployment_id: str | None = Query(None, description="Deployment ID for context"),
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update personnel information.

    Only admins and super admins can update personnel records.
    """
    # Check permissions
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can update personnel records",
        )

    if deployment_id:
        # Get personnel with deployment context
        personnel, override, notes = await get_personnel_by_id_with_deployment_context(
            personnel_id, deployment_id, user_id, user_role, db
        )

        # Update personnel fields
        update_data = personnel_update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if hasattr(personnel, field):
                setattr(personnel, field, value)

        await db.commit()
        await db.refresh(personnel)

        return PersonnelResponseWithDeployment(
            id=personnel.id,
            estab_id=personnel.estab_id,
            service_number=personnel.pers_no,
            rank=personnel.rank,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            deployment_id=deployment_id,
            has_override=override is not None,
            deployment_notes=notes.notes if notes else None,
        )
    else:
        # Update personnel without deployment context
        result = await db.execute(select(Personnel).where(Personnel.id == personnel_id))
        personnel = result.scalar_one_or_none()

        if not personnel:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Personnel not found",
            )

        # Update personnel fields
        update_data = personnel_update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if hasattr(personnel, field):
                setattr(personnel, field, value)

        await db.commit()
        await db.refresh(personnel)

        return PersonnelResponseWithDeployment(
            id=personnel.id,
            estab_id=personnel.estab_id,
            service_number=personnel.pers_no,
            rank=personnel.rank,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            deployment_id=None,
            has_override=False,
            deployment_notes=None,
        )


@router.get(
    "/personnel/{personnel_id}/attendance-history",
    response_model=PersonnelAttendanceHistoryResponse,
)
async def get_personnel_attendance_history(
    personnel_id: str,
    deployment_id: str = Query(..., description="Deployment ID for context"),
    date_from: date | None = Query(None, description="Filter attendance from this date"),
    date_to: date | None = Query(None, description="Filter attendance until this date"),
    limit: int = Query(100, ge=1, le=1000, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get attendance history for a personnel member within a deployment.

    Returns attendance records with summary statistics including:
    - Total sessions attended
    - Present/absent/excused/unknown counts
    - Attendance rate (present + excused / total)

    Supports date range filtering and pagination.
    """
    # Verify deployment access and personnel belongs to deployment
    personnel, override, notes = await get_personnel_by_id_with_deployment_context(
        personnel_id, deployment_id, user_id, user_role, db
    )

    # Build attendance query with session join
    query = (
        select(AttendanceRecord, Session)
        .join(Session, AttendanceRecord.session_id == Session.id)
        .where(
            and_(
                AttendanceRecord.personnel_id == personnel_id,
                Session.deployment_id == deployment_id,
            )
        )
    )

    # Apply date range filters
    if date_from:
        query = query.where(Session.date >= date_from)
    if date_to:
        query = query.where(Session.date <= date_to)

    # Get total count
    count_subquery = query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_result = await db.execute(count_query)
    total_count = total_result.scalar() or 0

    # Apply pagination
    query = query.offset(offset).limit(limit)

    # Order by session date descending (most recent first)
    query = query.order_by(Session.date.desc(), Session.session_type.desc())

    # Execute query
    result = await db.execute(query)
    records = result.all()

    # Build attendance history items
    attendance_items = []
    present_count = 0
    absent_count = 0
    excused_count = 0
    unknown_count = 0

    for record, session in records:
        # Count by status
        if record.status == "present":
            present_count += 1
        elif record.status == "absent":
            absent_count += 1
        elif record.status == "excused":
            excused_count += 1
        elif record.status == "unknown":
            unknown_count += 1

        attendance_items.append(
            PersonnelAttendanceHistoryItem(
                id=record.id,
                session_id=record.session_id,
                session_date=session.date,
                session_type=session.session_type,
                session_status=session.status,
                status=record.status,
                remarks=record.remarks,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

    # Calculate attendance rate
    # Attendance rate = (present + excused) / total_sessions
    total_sessions = present_count + absent_count + excused_count + unknown_count
    if total_sessions > 0:
        attendance_rate = ((present_count + excused_count) / total_sessions) * 100
    else:
        attendance_rate = 0.0

    # Build statistics
    stats = PersonnelAttendanceHistoryStats(
        total_sessions=total_sessions,
        present_count=present_count,
        absent_count=absent_count,
        excused_count=excused_count,
        unknown_count=unknown_count,
        attendance_rate=round(attendance_rate, 2),
    )

    # Build response
    return PersonnelAttendanceHistoryResponse(
        personnel_id=personnel_id,
        deployment_id=deployment_id,
        date_from=date_from,
        date_to=date_to,
        stats=stats,
        attendance_records=attendance_items,
        total_count=total_count,
    )
