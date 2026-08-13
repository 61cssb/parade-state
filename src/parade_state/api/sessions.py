"""Attendance session management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.api.access_control import get_user_accessible_deployments
from parade_state.api.attendance import is_retroactive_edit
from parade_state.db import get_db_session
from parade_state.models import Deployment, DeploymentUserAccess, Session
from parade_state.models.attendance import AttendanceRecord
from parade_state.models.deployment import (
    DeploymentNotes,
    DeploymentPersonnelExclusion,
    DeploymentPersonnelOverride,
)
from parade_state.models.personnel import Personnel
from parade_state.models.schemas import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
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
            status_code=status.HTTP_404_NOT_FOUND,
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
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions to access this deployment",
    )


async def verify_session_access(
    session_id: str,
    user_id: str,
    user_role: str,
    db: AsyncSession,
) -> Session:
    """Verify user has access to session and return it."""
    # Get session
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Verify deployment access
    await verify_deployment_access(session.deployment_id, user_id, user_role, db)

    return session


async def validate_session_status_transition(
    current_status: str,
    new_status: str,
) -> bool:
    """Validate session status transition is allowed."""
    # Sequential transitions with reopen path: open ↔ closed → finalized
    valid_transitions = {
        "open": ["closed"],
        "closed": ["open", "finalized"],  # May reopen or finalize
        "finalized": [],  # Finalized is terminal
    }

    return new_status in valid_transitions.get(current_status, [])


async def check_session_uniqueness(
    deployment_id: str,
    date: utc_dt.datetime,
    session_type: str,
    db: AsyncSession,
    exclude_session_id: str | None = None,
) -> bool:
    """Check if a session with the same deployment, date, and type already exists."""
    query = select(Session).where(
        and_(
            Session.deployment_id == deployment_id,
            Session.date == date,
            Session.session_type == session_type,
        )
    )

    if exclude_session_id:
        query = query.where(Session.id != exclude_session_id)

    result = await db.execute(query)
    existing_session = result.scalar_one_or_none()

    return existing_session is not None


# ============================================================================
# Session CRUD Endpoints
# ============================================================================


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    session_data: SessionCreate,
    user_id: str = Query(..., description="User ID creating the session"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new attendance session.

    Requires admin or super_admin role.
    Sessions can only be created for active deployments (not draft).
    Only one session per type (AM/PM) per deployment per day.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can create sessions",
        )

    # Verify deployment exists, user has access, and deployment is active
    deployment = await verify_deployment_access(
        session_data.deployment_id, user_id, user_role, db
    )

    if deployment.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Sessions can only be created for active deployments "
                f"(current status: '{deployment.status}')"
            ),
        )

    # Check session uniqueness (deployment + date + session_type)
    if await check_session_uniqueness(
        session_data.deployment_id,
        session_data.date,
        session_data.session_type,
        db,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A {session_data.session_type} session already exists for this deployment on this date",
        )

    # Create session
    now = utc_dt.utcnow()
    session = Session(
        deployment_id=session_data.deployment_id,
        date=session_data.date,
        session_type=session_data.session_type,
        status=session_data.status,
        created_by=user_id,
        opened_at=now,
    )

    try:
        db.add(session)
        await db.flush()  # Assign session.id without committing
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A {session_data.session_type} session already exists for this deployment on this date",
        ) from None

    # Auto-populate attendance records for the full deployment roster
    await _populate_session_roster(session, deployment, user_id, db)

    await db.commit()
    await db.refresh(session)

    return session


async def _populate_session_roster(
    session: Session,
    deployment: Deployment,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Create attendance records for all included personnel in the deployment roster.

    Uses batch queries to avoid N+1 when populating a large roster.
    Personnel default to 'absent' — admins/users mark them as present/excused later.
    """
    # Batch-load roster (active personnel belonging to the deployment's nominal roll)
    roster_result = await db.execute(
        select(Personnel).where(
            Personnel.nominal_roll_id == deployment.nominal_roll_id,
            Personnel.status == "active",
        )
    )
    all_roster = roster_result.scalars().all()

    if not all_roster:
        return

    # Batch-load exclusions for this deployment
    excl_result = await db.execute(
        select(DeploymentPersonnelExclusion.personnel_id).where(
            DeploymentPersonnelExclusion.deployment_id == str(deployment.id),
        )
    )
    excluded_ids = {str(row[0]) for row in excl_result.all()}

    # Batch-load overrides for snapshot data
    override_result = await db.execute(
        select(DeploymentPersonnelOverride).where(
            DeploymentPersonnelOverride.deployment_id == str(deployment.id),
        )
    )
    override_map = {
        str(o.personnel_id): o for o in override_result.scalars().all()
    }

    # Batch-load deployment notes for snapshot data
    notes_result = await db.execute(
        select(DeploymentNotes).where(
            DeploymentNotes.deployment_id == str(deployment.id),
        )
    )
    notes_map = {
        str(n.personnel_id): n.notes for n in notes_result.scalars().all()
    }

    is_retroactive = await is_retroactive_edit(session.date)

    for person in all_roster:
        if str(person.id) in excluded_ids:
            continue

        override = override_map.get(str(person.id))
        if override:
            unit = override.unit
            sub1 = override.sub_unit_1
            sub2 = override.sub_unit_2
            sub3 = override.sub_unit_3
        else:
            unit = person.unit
            sub1 = person.sub_unit_1
            sub2 = person.sub_unit_2
            sub3 = person.sub_unit_3

        db.add(
            AttendanceRecord(
                session_id=str(session.id),
                personnel_id=str(person.id),
                deployment_id=str(deployment.id),
                status="absent",
                notes_snapshot=notes_map.get(str(person.id)),
                unit_snapshot=unit,
                sub_unit_1_snapshot=sub1,
                sub_unit_2_snapshot=sub2,
                sub_unit_3_snapshot=sub3,
                created_by=user_id,
                updated_by=user_id,
                is_retroactive_edit=is_retroactive,
            )
        )


@router.get("/", response_model=list[SessionResponse])
async def list_sessions(
    deployment_id: str | None = None,
    status: str | None = None,
    date_from: utc_dt.datetime | None = None,
    date_to: utc_dt.datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for filtering"),
    db: AsyncSession = Depends(get_db_session),
):
    """List sessions with optional filtering.

    All authenticated users can list sessions.
    Filters may be applied based on user role.
    Non-super-admins can only see sessions from deployments they have access to.
    """
    query = select(Session)

    # For non-super-admins, filter by deployments they have access to
    if user_role != "super_admin":
        # Get user's accessible deployments
        accessible_deployments = await get_user_accessible_deployments(
            user_id, user_role, db
        )
        accessible_deployment_ids = [d.id for d in accessible_deployments]

        if not accessible_deployment_ids:
            return []  # No access to any deployments

        query = query.where(Session.deployment_id.in_(accessible_deployment_ids))

    # Apply filters
    if deployment_id:
        # Verify deployment access if specific deployment is requested
        await verify_deployment_access(deployment_id, user_id, user_role, db)
        query = query.where(Session.deployment_id == deployment_id)

    if status:
        query = query.where(Session.status == status)

    if date_from:
        query = query.where(Session.date >= date_from)

    if date_to:
        query = query.where(Session.date <= date_to)

    # Order by date and created_at descending
    query = query.order_by(Session.date.desc(), Session.created_at.desc())

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    sessions = result.scalars().all()

    return sessions


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a specific session by ID.

    Requires appropriate access permissions.
    """
    session = await verify_session_access(session_id, user_id, user_role, db)
    return session


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    update_data: SessionUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update a session (typically status changes: open → closed → finalized).

    Requires admin or super_admin role.
    Finalized sessions cannot be modified.
    """
    # Verify user has permission
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can update sessions",
        )

    # Get session
    session = await verify_session_access(session_id, user_id, user_role, db)

    # Prevent modifications to finalized sessions
    if session.status == "finalized":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify finalized sessions",
        )

    # Handle status transition
    if update_data.status is not None:
        current_status = session.status
        new_status = update_data.status

        # Validate transition
        if not await validate_session_status_transition(current_status, new_status):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status transition from {current_status} to {new_status}",
            )

        # Handle closing session
        if new_status == "closed" and current_status == "open":
            session.closed_at = utc_dt.utcnow()
            session.closed_by = user_id

        # Handle reopening a previously-closed session
        if new_status == "open" and current_status == "closed":
            session.closed_at = None
            session.closed_by = None

        # Handle finalizing session
        if new_status == "finalized":
            # Set closed_at if not already set
            if session.closed_at is None:
                session.closed_at = utc_dt.utcnow()
                session.closed_by = user_id

        session.status = new_status

    await db.commit()
    await db.refresh(session)

    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user_id: str = Query(..., description="User ID making the deletion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a session.

    Requires super_admin role.
    Finalized sessions cannot be deleted.
    """
    # Verify user has permission
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can delete sessions",
        )

    # Get session
    session = await verify_session_access(session_id, user_id, user_role, db)

    # Prevent deletion of finalized sessions
    if session.status == "finalized":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete finalized sessions",
        )

    await db.delete(session)
    await db.commit()

    return None
