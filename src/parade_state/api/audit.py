"""Audit log API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import AuditLog, User
from parade_state.models.schemas import AuditLogListItem, AuditLogListResponse

router = APIRouter()


@router.get("/logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    entity_type: str | None = Query(None, description="Filter by entity type"),
    action: str | None = Query(None, description="Filter by action"),
    target_user_id: str | None = Query(
        None, description="Filter to entries created by this user"
    ),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Query(..., description="Requesting user ID"),
    user_role: str = Query(..., description="Requesting user role"),
    db: AsyncSession = Depends(get_db_session),
) -> AuditLogListResponse:
    """List audit log entries with optional filtering and pagination.

    Returns entries ordered by timestamp desc (newest first).
    User name/email are resolved via left outer join on User.

    Requires admin or super_admin role.
    """
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can view audit logs",
        )

    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Build filter conditions (reused for both data and count queries)
    conditions = []
    if entity_type is not None:
        conditions.append(AuditLog.entity_type == entity_type)
    if action is not None:
        conditions.append(AuditLog.action == action)
    if target_user_id is not None:
        conditions.append(AuditLog.user_id == target_user_id)

    # Data query: join User for name/email resolution
    data_query = (
        select(AuditLog, User)
        .join(User, AuditLog.user_id == User.id, isouter=True)
        .order_by(AuditLog.timestamp.desc())
        .offset(offset)
        .limit(limit)
    )
    for cond in conditions:
        data_query = data_query.where(cond)

    result = await db.execute(data_query)
    rows = result.all()

    items = [
        AuditLogListItem(
            id=log.id,
            timestamp=log.timestamp,
            user_id=log.user_id,
            user_name=user_obj.name if user_obj else None,
            user_email=user_obj.email if user_obj else None,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            action=log.action,
            changes=log.changes,
            description=log.description,
            ip_address=log.ip_address,
        )
        for log, user_obj in rows
    ]

    # Count query (same filters, no join needed)
    count_query = select(func.count()).select_from(AuditLog)
    for cond in conditions:
        count_query = count_query.where(cond)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    return AuditLogListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
