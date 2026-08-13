"""Personnel management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import (
    PRESENT_LIKE_STATUSES,
    Attendance,
    Deployment,
    DeploymentNotes,
    DeploymentPersonnelExclusion,
    DeploymentPersonnelOverride,
    DeploymentUserAccess,
    Personnel,
)
from parade_state.models.schemas import (
    PersonnelAttendanceHistoryItem,
    PersonnelAttendanceHistoryResponse,
    PersonnelAttendanceHistoryStats,
    PersonnelListParams,
    PersonnelResponseWithDeployment,
    PersonnelUpdate,
)
from parade_state.utils import ranks, utc_dt

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
    # Filter by nominal_roll_id
    if params.nominal_roll_id:
        query = query.where(Personnel.nominal_roll_id == params.nominal_roll_id)

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

    # Filter by category (Officer / WOSE)
    if params.category:
        query = query.where(Personnel.category == params.category)

    # Search across name and short_id
    if params.search:
        search_term = f"%{params.search}%"
        query = query.where(
            or_(
                Personnel.full_name.ilike(search_term),
                Personnel.short_id.ilike(search_term),
            )
        )

    # Apply sorting
    if params.sort_by:
        # Map sort_by parameter to actual model fields
        sort_field_map = {
            "name": Personnel.full_name,
            "rank": Personnel.rank,
            "unit": Personnel.unit,
            "status": Personnel.status,
            "created_at": Personnel.created_at,
            "updated_at": Personnel.updated_at,
        }

        if params.sort_by in sort_field_map:
            sort_field = sort_field_map[params.sort_by]

            # Apply sort order
            if params.sort_order == "desc":
                query = query.order_by(sort_field.desc())
            else:
                query = query.order_by(sort_field.asc())

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
    - Deployment-specific personnel (from nominal roll)
    - Personnel overrides for this deployment
    - Unit hierarchy filtering
    - Search functionality
    - Deployment notes integration
    """
    # Verify deployment exists and user has access
    deployment = await verify_deployment_access(deployment_id, user_id, user_role, db)

    # Get base personnel query
    query = select(Personnel).where(
        Personnel.nominal_roll_id == deployment.nominal_roll_id
    )

    # Exclude personnel filtered out for this deployment
    excluded_subq = select(DeploymentPersonnelExclusion.personnel_id).where(
        DeploymentPersonnelExclusion.deployment_id == deployment_id
    )
    query = query.where(~Personnel.id.in_(excluded_subq))

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
            nominal_roll_id=personnel.nominal_roll_id,
            short_id=personnel.short_id,
            rank=personnel.rank,
            category=personnel.category,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            updated_at=personnel.updated_at,
            created_by=personnel.created_by,
            updated_by=personnel.updated_by,
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

    # Verify personnel belongs to deployment's nominal roll
    if personnel.nominal_roll_id != deployment.nominal_roll_id:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Personnel does not belong to this deployment's nominal roll",
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
    nominal_roll_id: str | None = Query(
        None, description="Filter by nominal roll ID"
    ),
    unit: str | None = Query(None, description="Filter by unit"),
    sub_unit_1: str | None = Query(None, description="Filter by sub-unit 1"),
    sub_unit_2: str | None = Query(None, description="Filter by sub-unit 2"),
    sub_unit_3: str | None = Query(None, description="Filter by sub-unit 3"),
    status: str | None = Query(None, description="Filter by personnel status"),
    category: str | None = Query(
        None, description="Filter by category (Officer, WOSE)"
    ),
    search: str | None = Query(None, description="Search by name or service number"),
    sort_by: str | None = Query(
        None,
        description="Sort field (name, rank, unit, status, created_at, updated_at)",
    ),
    sort_order: str | None = Query(None, description="Sort order (asc, desc)"),
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

    Sorting:
    - Can sort by: name, rank, unit, status, created_at, updated_at
    - Sort order: asc (ascending) or desc (descending)
    - Default: No sorting (returns in natural order)
    """
    params = PersonnelListParams(
        deployment_id=deployment_id,
        nominal_roll_id=nominal_roll_id,
        unit=unit,
        sub_unit_1=sub_unit_1,
        sub_unit_2=sub_unit_2,
        sub_unit_3=sub_unit_3,
        status=status,
        category=category,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )

    # Deployment-scoped query
    if params.deployment_id:
        (
            personnel_list,
            _,
            _,
        ) = await get_deployment_personnel_with_overrides(
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

    # Apply pagination
    query = query.offset(params.offset).limit(params.limit)

    # Execute query
    result = await db.execute(query)
    personnel_list = result.scalars().all()

    # Build response without deployment context
    personnel_responses = [
        PersonnelResponseWithDeployment(
            id=p.id,
            nominal_roll_id=p.nominal_roll_id,
            short_id=p.short_id,
            rank=p.rank,
            category=p.category,
            name=p.full_name,
            unit=p.unit,
            sub_unit_1=p.sub_unit_1,
            sub_unit_2=p.sub_unit_2,
            sub_unit_3=p.sub_unit_3,
            status=p.status,
            created_at=p.created_at,
            updated_at=p.updated_at,
            created_by=p.created_by,
            updated_by=p.updated_by,
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
            nominal_roll_id=personnel.nominal_roll_id,
            short_id=personnel.short_id,
            rank=personnel.rank,
            category=personnel.category,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            updated_at=personnel.updated_at,
            created_by=personnel.created_by,
            updated_by=personnel.updated_by,
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
            nominal_roll_id=personnel.nominal_roll_id,
            short_id=personnel.short_id,
            rank=personnel.rank,
            category=personnel.category,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            updated_at=personnel.updated_at,
            created_by=personnel.created_by,
            updated_by=personnel.updated_by,
            deployment_id=None,
            has_override=False,
            deployment_notes=None,
        )


@router.patch(
    "/personnel/{personnel_id}", response_model=PersonnelResponseWithDeployment
)
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

        # Category is always inferred from rank; recompute if rank changed.
        if "rank" in update_data:
            try:
                personnel.category = ranks.category_for_rank(personnel.rank)
            except ValueError as e:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid rank {personnel.rank!r}: cannot infer category",
                ) from e

        # Set audit trail fields
        personnel.updated_at = utc_dt.utcnow()
        personnel.updated_by = user_id

        await db.commit()
        await db.refresh(personnel)

        return PersonnelResponseWithDeployment(
            id=personnel.id,
            nominal_roll_id=personnel.nominal_roll_id,
            short_id=personnel.short_id,
            rank=personnel.rank,
            category=personnel.category,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            updated_at=personnel.updated_at,
            created_by=personnel.created_by,
            updated_by=personnel.updated_by,
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

        # Category is always inferred from rank; recompute if rank changed.
        if "rank" in update_data:
            try:
                personnel.category = ranks.category_for_rank(personnel.rank)
            except ValueError as e:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid rank {personnel.rank!r}: cannot infer category",
                ) from e

        # Set audit trail fields
        personnel.updated_at = utc_dt.utcnow()
        personnel.updated_by = user_id

        await db.commit()
        await db.refresh(personnel)

        return PersonnelResponseWithDeployment(
            id=personnel.id,
            nominal_roll_id=personnel.nominal_roll_id,
            short_id=personnel.short_id,
            rank=personnel.rank,
            category=personnel.category,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            created_at=personnel.created_at,
            updated_at=personnel.updated_at,
            created_by=personnel.created_by,
            updated_by=personnel.updated_by,
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
    nominal_roll_id: str | None = Query(
        None, description="Optional NR scope (must match the personnel's NR)"
    ),
    date_from: utc_dt.date | None = Query(
        None, description="Filter attendance from this date"
    ),
    date_to: utc_dt.date | None = Query(
        None, description="Filter attendance until this date"
    ),
    limit: int = Query(100, ge=1, le=1000, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get attendance history for a personnel member.

    Returns per-day AM/PM attendance with summary statistics. AM and PM slots
    are counted independently toward totals. Supports date range filtering and
    pagination.
    """
    # Resolve personnel (and its NR).
    personnel_result = await db.execute(
        select(Personnel).where(Personnel.id == personnel_id)
    )
    personnel = personnel_result.scalar_one_or_none()
    if not personnel:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Personnel not found",
        )

    resolved_nr = personnel.nominal_roll_id
    if nominal_roll_id and nominal_roll_id != resolved_nr:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Personnel does not belong to this nominal roll",
        )

    # Build attendance query (NR/Tagging-scoped, no sessions).
    query = select(Attendance).where(Attendance.personnel_id == personnel_id)
    if date_from:
        query = query.where(Attendance.date >= date_from)
    if date_to:
        query = query.where(Attendance.date <= date_to)

    # Total count (before pagination).
    count_subquery = query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_count = (await db.execute(count_query)).scalar() or 0

    query = query.offset(offset).limit(limit).order_by(Attendance.date.desc())

    result = await db.execute(query)
    records = list(result.scalars().all())

    # Build items + stats (AM and PM each count as one slot).
    attendance_items = []
    present_count = 0
    absent_count = 0

    for record in records:
        for slot_value in (record.status_am, record.status_pm):
            if slot_value in PRESENT_LIKE_STATUSES:
                present_count += 1
            else:
                absent_count += 1

        attendance_items.append(
            PersonnelAttendanceHistoryItem(
                id=record.id,
                nominal_roll_id=record.nominal_roll_id,
                tagging_id=record.tagging_id,
                date=record.date,
                status_am=record.status_am,
                remarks_am=record.remarks_am,
                status_pm=record.status_pm,
                remarks_pm=record.remarks_pm,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

    total_slots = present_count + absent_count
    attendance_rate = (present_count / total_slots * 100) if total_slots else 0.0

    stats = PersonnelAttendanceHistoryStats(
        total_slots=total_slots,
        present_count=present_count,
        absent_count=absent_count,
        attendance_rate=round(attendance_rate, 2),
    )

    return PersonnelAttendanceHistoryResponse(
        personnel_id=personnel_id,
        nominal_roll_id=resolved_nr,
        date_from=date_from,
        date_to=date_to,
        stats=stats,
        attendance_records=attendance_items,
        total_count=total_count,
    )
