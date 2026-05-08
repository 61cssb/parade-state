"""Attendance management API endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models.attendance import AttendanceRecord, Session
from parade_state.models.deployment import Deployment, DeploymentNotes, DeploymentPersonnelOverride
from parade_state.models.personnel import Personnel
from parade_state.models.schemas import (
    AttendanceRecordCreate,
    AttendanceRecordUpdate,
    AttendanceRecordResponse,
    AttendanceRecordBulkCreate,
    AttendanceRecordBulkUpdate,
    AttendanceListParams,
)
from parade_state.utils import utc_dt

router = APIRouter()


# ============================================================================
# Helper Functions
# ============================================================================


async def verify_attendance_access(
    attendance_id: str,
    user_id: str,
    user_role: str,
    db: AsyncSession,
) -> AttendanceRecord:
    """Verify user has access to attendance record and return it."""
    # Super admins have full access
    if user_role == "super_admin":
        result = await db.execute(select(AttendanceRecord).where(AttendanceRecord.id == attendance_id))
        attendance = result.scalar_one_or_none()
        if not attendance:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Attendance record not found",
            )
        return attendance

    # For regular users and admins, check attendance access based on scope
    # TODO: Implement proper access control based on deployment/subunit scope
    # For now, admins can access all attendance records
    if user_role in ["admin", "user"]:
        result = await db.execute(select(AttendanceRecord).where(AttendanceRecord.id == attendance_id))
        attendance = result.scalar_one_or_none()
        if not attendance:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Attendance record not found",
            )
        return attendance

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions to access this attendance record",
    )


async def verify_session_is_open(
    session_id: str,
    db: AsyncSession,
) -> Session:
    """Verify that a session is open for attendance recording and return it."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.status != "open":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot modify attendance for {session.status} sessions",
        )

    return session


async def is_retroactive_edit(
    session_date: datetime | date,
) -> bool:
    """Determine if an edit is retroactive (session date is in the past).

    Uses UTC timezone for consistent comparison.
    Handles both datetime and date objects.
    """
    # Get current UTC time
    now = utc_dt.utcnow()
    now_naive = utc_dt.ensure_naive(now)

    # Handle both date and datetime objects
    if isinstance(session_date, datetime):
        session_date_naive = utc_dt.ensure_naive(session_date)
        return session_date_naive.date() < now_naive.date()
    else:
        # It's already a date object
        return session_date < now_naive.date()


async def get_personnel_snapshot_data(
    personnel_id: str,
    deployment_id: str,
    db: AsyncSession,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Get personnel assignment snapshot data, considering deployment overrides."""
    # Check for deployment override first
    override_result = await db.execute(
        select(DeploymentPersonnelOverride).where(
            and_(
                DeploymentPersonnelOverride.deployment_id == deployment_id,
                DeploymentPersonnelOverride.personnel_id == personnel_id,
            )
        )
    )
    override = override_result.scalar_one_or_none()

    if override:
        # Use override data
        return override.unit, override.sub_unit_1, override.sub_unit_2, override.sub_unit_3

    # Fall back to base personnel data
    personnel_result = await db.execute(select(Personnel).where(Personnel.id == personnel_id))
    personnel = personnel_result.scalar_one_or_none()

    if personnel:
        return personnel.unit, personnel.sub_unit_1, personnel.sub_unit_2, personnel.sub_unit_3

    return None, None, None, None


async def get_deployment_notes_snapshot(
    personnel_id: str,
    deployment_id: str,
    db: AsyncSession,
) -> str | None:
    """Get deployment notes snapshot for a personnel member."""
    result = await db.execute(
        select(DeploymentNotes).where(
            and_(
                DeploymentNotes.deployment_id == deployment_id,
                DeploymentNotes.personnel_id == personnel_id,
            )
        )
    )
    notes = result.scalar_one_or_none()

    return notes.notes if notes else None


# ============================================================================
# Attendance CRUD Endpoints
# ============================================================================


@router.post("/", response_model=AttendanceRecordResponse, status_code=status.HTTP_201_CREATED)
async def create_attendance_record(
    attendance_data: AttendanceRecordCreate,
    user_id: str = Query(..., description="User ID creating the attendance record"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new attendance record.

    Requires the session to be open for attendance recording.
    Automatically snapshots deployment notes and personnel assignments.
    """
    # Verify session is open and get session object
    session = await verify_session_is_open(attendance_data.session_id, db)

    # Check if attendance record already exists
    existing_result = await db.execute(
        select(AttendanceRecord).where(
            and_(
                AttendanceRecord.session_id == attendance_data.session_id,
                AttendanceRecord.personnel_id == attendance_data.personnel_id,
            )
        )
    )
    existing_attendance = existing_result.scalar_one_or_none()

    if existing_attendance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attendance record already exists for this personnel and session. Use update endpoint.",
        )

    # Get snapshot data
    unit, sub_unit_1, sub_unit_2, sub_unit_3 = await get_personnel_snapshot_data(
        attendance_data.personnel_id, session.deployment_id, db
    )
    notes_snapshot = await get_deployment_notes_snapshot(
        attendance_data.personnel_id, session.deployment_id, db
    )

    # Check if this is a retroactive edit (session date is in the past)
    is_retroactive = await is_retroactive_edit(session.date)

    # Create attendance record
    now = utc_dt.utcnow()
    attendance = AttendanceRecord(
        session_id=attendance_data.session_id,
        personnel_id=attendance_data.personnel_id,
        deployment_id=session.deployment_id,
        status=attendance_data.status,
        remarks=attendance_data.remarks,
        notes_snapshot=notes_snapshot,
        unit_snapshot=unit,
        sub_unit_1_snapshot=sub_unit_1,
        sub_unit_2_snapshot=sub_unit_2,
        sub_unit_3_snapshot=sub_unit_3,
        created_by=user_id,
        updated_by=user_id,
        is_retroactive_edit=is_retroactive,
    )

    try:
        db.add(attendance)
        await db.commit()
        await db.refresh(attendance)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create attendance record. Invalid session or personnel ID.",
        )

    return attendance


@router.get("/", response_model=list[AttendanceRecordResponse])
async def list_attendance_records(
    session_id: str | None = None,
    deployment_id: str | None = None,
    personnel_id: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for filtering"),
    db: AsyncSession = Depends(get_db_session),
):
    """List attendance records with optional filtering.

    All authenticated users can list attendance records.
    Filters may be applied based on user role and scope.
    """
    query = select(AttendanceRecord)

    # Apply filters
    if session_id:
        query = query.where(AttendanceRecord.session_id == session_id)

    if deployment_id:
        query = query.where(AttendanceRecord.deployment_id == deployment_id)

    if personnel_id:
        query = query.where(AttendanceRecord.personnel_id == personnel_id)

    if status:
        query = query.where(AttendanceRecord.status == status)

    # Order by created_at descending
    query = query.order_by(AttendanceRecord.created_at.desc())

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    attendance_records = result.scalars().all()

    return attendance_records


@router.get("/{attendance_id}", response_model=AttendanceRecordResponse)
async def get_attendance_record(
    attendance_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a specific attendance record by ID.

    Requires appropriate access permissions.
    """
    attendance = await verify_attendance_access(attendance_id, user_id, user_role, db)
    return attendance


@router.patch("/{attendance_id}", response_model=AttendanceRecordResponse)
async def update_attendance_record(
    attendance_id: str,
    update_data: AttendanceRecordUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update an attendance record.

    Requires the session to be open for attendance modifications.
    Tracks retroactive edits.
    """
    # Get attendance record
    attendance = await verify_attendance_access(attendance_id, user_id, user_role, db)

    # Verify session is open and get session object
    session = await verify_session_is_open(attendance.session_id, db)

    # Check if this is a retroactive edit
    is_retroactive = await is_retroactive_edit(session.date)

    # Update fields
    if update_data.status is not None:
        attendance.status = update_data.status

    if update_data.remarks is not None:
        attendance.remarks = update_data.remarks

    # Update audit trail
    attendance.updated_by = user_id
    attendance.updated_at = utc_dt.utcnow()
    attendance.last_edit_at = utc_dt.utcnow()
    attendance.last_edit_by = user_id

    # Update retroactive flag
    if is_retroactive:
        attendance.is_retroactive_edit = True

    await db.commit()
    await db.refresh(attendance)

    return attendance


@router.delete("/{attendance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attendance_record(
    attendance_id: str,
    user_id: str = Query(..., description="User ID making the deletion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete an attendance record.

    Requires admin or super_admin role.
    Session must be open.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can delete attendance records",
        )

    # Get attendance record
    attendance = await verify_attendance_access(attendance_id, user_id, user_role, db)

    # Verify session is open (discard session object as we don't need it)
    await verify_session_is_open(attendance.session_id, db)

    await db.delete(attendance)
    await db.commit()

    return None


# ============================================================================
# Bulk Operations
# ============================================================================


@router.post("/bulk/create", response_model=list[AttendanceRecordResponse], status_code=status.HTTP_201_CREATED)
async def bulk_create_attendance(
    bulk_data: AttendanceRecordBulkCreate,
    user_id: str = Query(..., description="User ID creating the attendance records"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Bulk create attendance records.

    All creations are performed atomically - if any fails, all changes are rolled back.
    Requires the sessions to be open for attendance recording.
    Automatically snapshots deployment notes and personnel assignments.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can perform bulk attendance creation",
        )

    created_records = []

    try:
        # Get all unique session IDs to verify they're open
        session_ids = list(set([item.session_id for item in bulk_data.attendance_records]))

        # Verify all sessions are open
        # Session is now imported at the top level
        session_results = await db.execute(
            select(Session).where(Session.id.in_(session_ids))
        )
        sessions = {s.id: s for s in session_results.scalars().all()}

        # Check all sessions exist and are open
        for session_id in session_ids:
            if session_id not in sessions:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Session {session_id} not found",
                )
            if sessions[session_id].status != "open":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Session {session_id} is not open",
                )

        # Process each attendance record creation
        for attendance_item in bulk_data.attendance_records:
            session = sessions[attendance_item.session_id]

            # Check if attendance record already exists
            existing_result = await db.execute(
                select(AttendanceRecord).where(
                    and_(
                        AttendanceRecord.session_id == attendance_item.session_id,
                        AttendanceRecord.personnel_id == attendance_item.personnel_id,
                    )
                )
            )
            existing_attendance = existing_result.scalar_one_or_none()

            if existing_attendance:
                # Skip existing records or raise error - let's skip for bulk operations
                continue

            # Get snapshot data
            unit, sub_unit_1, sub_unit_2, sub_unit_3 = await get_personnel_snapshot_data(
                attendance_item.personnel_id, session.deployment_id, db
            )
            notes_snapshot = await get_deployment_notes_snapshot(
                attendance_item.personnel_id, session.deployment_id, db
            )

            # Check if this is a retroactive edit
            is_retroactive = await is_retroactive_edit(session.date)

            # Create attendance record
            attendance = AttendanceRecord(
                session_id=attendance_item.session_id,
                personnel_id=attendance_item.personnel_id,
                deployment_id=session.deployment_id,
                status=attendance_item.status,
                remarks=attendance_item.remarks,
                notes_snapshot=notes_snapshot,
                unit_snapshot=unit,
                sub_unit_1_snapshot=sub_unit_1,
                sub_unit_2_snapshot=sub_unit_2,
                sub_unit_3_snapshot=sub_unit_3,
                created_by=user_id,
                updated_by=user_id,
                is_retroactive_edit=is_retroactive,
            )

            db.add(attendance)
            created_records.append(attendance)

        await db.commit()

        # Refresh all created records
        for record in created_records:
            await db.refresh(record)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bulk creation failed: {str(e)}",
        )

    return created_records


@router.post("/bulk/update", response_model=list[AttendanceRecordResponse])
async def bulk_update_attendance(
    bulk_data: AttendanceRecordBulkUpdate,
    user_id: str = Query(..., description="User ID making the bulk update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Bulk update attendance records.

    All updates are performed atomically - if any fails, all changes are rolled back.
    Requires the sessions to be open for attendance modifications.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can perform bulk attendance updates",
        )

    updated_records = []

    try:
        # Get all attendance records to update
        attendance_ids = [item.id for item in bulk_data.attendance_records]

        # Fetch all records and their sessions
        results = await db.execute(
            select(AttendanceRecord, Session)
            .join(Session, AttendanceRecord.session_id == Session.id)
            .where(AttendanceRecord.id.in_(attendance_ids))
        )

        records_data = {r[0].id: (r[0], r[1]) for r in results.all()}

        # Process each update
        for update_item in bulk_data.attendance_records:
            if update_item.id not in records_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Attendance record {update_item.id} not found",
                )

            attendance, session = records_data[update_item.id]

            # Verify session is open
            if session.status != "open":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot modify attendance for {session.status} session {session.id}",
                )

            # Update fields
            if update_item.status is not None:
                attendance.status = update_item.status

            if update_item.remarks is not None:
                attendance.remarks = update_item.remarks

            # Update audit trail
            attendance.updated_by = user_id
            attendance.updated_at = utc_dt.utcnow()
            attendance.last_edit_at = utc_dt.utcnow()
            attendance.last_edit_by = user_id

            # Check if this is a retroactive edit
            is_retroactive = await is_retroactive_edit(session.date)
            if is_retroactive:
                attendance.is_retroactive_edit = True

            updated_records.append(attendance)

        await db.commit()

        # Refresh all updated records
        for record in updated_records:
            await db.refresh(record)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bulk update failed: {str(e)}",
        )

    return updated_records