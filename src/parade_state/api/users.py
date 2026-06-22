"""User management endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.dependencies import (
    require_admin_user,
    require_authenticated_user,
)
from parade_state.db import get_db_session
from parade_state.models import AccessLevel, AuditLog, User
from parade_state.utils import ids


class UserUpdate(BaseModel):
    """Schema for user updates."""

    name: str | None = None
    status: str | None = None
    role: str | None = None
    access_level_id: str | None = None


router = APIRouter()


@router.get("/")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: str | None = None,
    status_filter: str | None = None,
    role_filter: str | None = None,
    current_user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List all users with optional filtering (admin only)."""

    # Build base query
    query = select(User)

    # Apply filters
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                User.email.ilike(search_pattern),
                User.name.ilike(search_pattern),
            )
        )

    if status_filter:
        query = query.where(User.status == status_filter)

    if role_filter:
        query = query.where(User.role == role_filter)

    # Apply pagination
    query = query.offset(skip).limit(limit)

    # Execute query
    result = await db.execute(query)
    users = result.scalars().all()

    # Get total count
    count_query = select(User)
    if search:
        search_pattern = f"%{search}%"
        count_query = count_query.where(
            or_(
                User.email.ilike(search_pattern),
                User.name.ilike(search_pattern),
            )
        )
    if status_filter:
        count_query = count_query.where(User.status == status_filter)
    if role_filter:
        count_query = count_query.where(User.role == role_filter)

    count_result = await db.execute(count_query)
    total_count = len(count_result.scalars().all())

    return {
        "users": [
            {
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "status": user.status,
                "access_level_id": str(user.access_level_id)
                if user.access_level_id
                else None,
                "created_at": user.created_at.isoformat(),
                "last_sign_in_at": user.last_sign_in_at.isoformat()
                if user.last_sign_in_at
                else None,
            }
            for user in users
        ],
        "total_count": total_count,
        "skip": skip,
        "limit": limit,
    }


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    current_user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a specific user by ID."""

    # Check permission: users can view themselves, admins can view anyone
    if (
        current_user.role not in ["admin", "super_admin"]
        and str(current_user.id) != user_id
    ):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You can only view your own profile",
        )

    # Validate UUID format but keep as string for database comparison
    try:
        ids.validate(user_id)  # Validate format
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format",
        ) from None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "status": user.status,
        "access_level_id": str(user.access_level_id) if user.access_level_id else None,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
        "first_sign_in_at": user.first_sign_in_at.isoformat()
        if user.first_sign_in_at
        else None,
        "last_sign_in_at": user.last_sign_in_at.isoformat()
        if user.last_sign_in_at
        else None,
    }


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    update_data: UserUpdate,
    current_user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Update user information (admin only)."""

    # Validate UUID format but keep as string for database comparison
    try:
        ids.validate(user_id)  # Validate format
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format",
        ) from None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Track changes for audit log
    changes = []

    # Update fields
    if update_data.name is not None:
        changes.append(f"name: '{user.name}' -> '{update_data.name}'")
        user.name = update_data.name

    if update_data.status is not None:
        if update_data.status not in ["pending", "active", "suspended", "unrecognised"]:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Invalid status value",
            )
        changes.append(f"status: '{user.status}' -> '{update_data.status}'")
        user.status = update_data.status

    if update_data.role is not None:
        if update_data.role not in ["super_admin", "admin", "user"]:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Invalid role value",
            )

        # Only super_admin can promote to super_admin
        if update_data.role == "super_admin" and current_user.role != "super_admin":
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Only super admins can grant super admin role",
            )
        changes.append(f"role: '{user.role}' -> '{update_data.role}'")
        user.role = update_data.role

    if update_data.access_level_id is not None:
        try:
            ids.validate(update_data.access_level_id)  # Validate format
            # Verify access level exists
            access_result = await db.execute(
                select(AccessLevel).where(AccessLevel.id == update_data.access_level_id)
            )
            access_level = access_result.scalar_one_or_none()

            if not access_level:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail="Access level not found",
                )

            changes.append(f"access_level_id: '{user.access_level_id}' -> '{update_data.access_level_id}'")
            user.access_level_id = update_data.access_level_id
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Invalid access level ID format",
            ) from None

    # Create audit log entry if any changes were made
    if changes:
        audit_log = AuditLog(
            user_id=str(current_user.id),
            entity_type="user",
            entity_id=user_id,
            action="update",
            description=f"Updated user '{user.email}': {', '.join(changes)}",
        )
        db.add(audit_log)

    await db.commit()
    await db.refresh(user)

    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "status": user.status,
        "access_level_id": str(user.access_level_id) if user.access_level_id else None,
        "updated_at": user.updated_at.isoformat(),
    }


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_user: User = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a user (admin only)."""

    # Only super_admin can delete users
    if current_user.role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only super admins can delete users",
        )

    # Validate UUID format but keep as string for database comparison
    try:
        ids.validate(user_id)  # Validate format
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format",
        ) from None

    # Prevent self-deletion
    if str(current_user.id) == user_id:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_email = user.email

    await db.delete(user)

    # Create audit log entry (after delete, before commit)
    audit_log = AuditLog(
        user_id=str(current_user.id),
        entity_type="user",
        entity_id=user_id,
        action="delete",
        description=f"Deleted user '{user_email}'",
    )
    db.add(audit_log)

    await db.commit()

    return {"message": "User deleted successfully"}
