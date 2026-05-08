"""Authentication middleware for protected routes."""

from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from parade_state.db import get_db_session
from parade_state.models import User
from parade_state.session import get_valid_session
from parade_state.utils import uuid_gen


security = HTTPBearer()


async def get_current_user_optional(
    request: Request,
) -> Optional[User]:
    """Get current user from session without requiring authentication."""
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # Remove "Bearer " prefix

    async for db in get_db_session():
        session = await get_valid_session(db, token, update_last_accessed=True)
        if not session:
            return None

        result = await db.execute(select(User).where(User.id == uuid_gen.to_uuid(session.user_id)))
        user = result.scalar_one_or_none()

        if user and user.status == "active":
            return user

    return None


async def require_authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Require authenticated user for protected endpoints."""
    token = credentials.credentials

    async for db in get_db_session():
        session = await get_valid_session(db, token, update_last_accessed=True)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        result = await db.execute(select(User).where(User.id == uuid_gen.to_uuid(session.user_id)))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User account is {user.status}",
            )

        return user

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Database connection error",
    )


async def require_admin_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Require admin user for admin-only endpoints."""
    user = await require_authenticated_user(request, credentials)

    if user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user


async def require_super_admin_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Require super admin user for super-admin-only endpoints."""
    user = await require_authenticated_user(request, credentials)

    if user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )

    return user


def check_access_level(required_access_level_order: int):
    """Dependency factory to check user access level."""
    async def check_access(
        request: Request,
        credentials: HTTPAuthorizationCredentials = Depends(security),
    ) -> User:
        user = await require_authenticated_user(request, credentials)

        if not user.access_level_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access level assigned",
            )

        # This would require fetching the AccessLevel to check the level_order
        # For now, just ensure user has some access level
        return user

    return check_access