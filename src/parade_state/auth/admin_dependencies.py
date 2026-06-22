"""Flexible authentication dependencies for admin interface.

This module provides authentication dependencies that work with multiple
token sources (Authorization header, cookie, query param) for the admin interface.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from parade_state.auth.session import get_valid_session
from parade_state.db import get_db_session
from parade_state.models import User
from parade_state.utils import cookies

security = HTTPBearer(auto_error=False)


async def get_token_from_request(request: Request) -> str | None:
    """Extract token from multiple sources.

    Tries to extract token from:
    1. Authorization header (Bearer token)
    2. Cookie (session_token)
    3. Query parameter (token)

    Args:
        request: FastAPI Request object

    Returns:
        Token string if found, None otherwise
    """
    # Try Authorization header first
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    # Try cookie using centralized utility
    token = cookies.get_auth_token(request)
    if token:
        return token

    # Try query parameter (for testing)
    token = request.query_params.get("token")
    if token:
        return token

    return None


async def get_current_admin_user_optional(
    request: Request,
) -> User | None:
    """Get current admin user from session without requiring authentication.

    Extracts token from multiple sources and validates it,
    but returns None instead of raising exception if not authenticated.

    Args:
        request: FastAPI Request object

    Returns:
        User object if authenticated and valid admin, None otherwise
    """
    token = await get_token_from_request(request)
    if not token:
        return None

    # Get database session maker
    from parade_state.db import get_session_maker

    session_maker = get_session_maker()
    if not session_maker:
        return None

    async with session_maker() as db:
        session = await get_valid_session(db, token, update_last_accessed=True)
        if not session:
            return None

        result = await db.execute(select(User).where(User.id == session.user_id))
        user = result.scalar_one_or_none()

        if user and user.status == "active" and user.role in ["admin", "super_admin"]:
            return user

    return None


async def require_admin_user_flexible(
    request: Request,
) -> User:
    """Require admin user for protected endpoints (flexible token sources).

    Validates authentication and checks if user has admin or super_admin role.
    Accepts tokens from multiple sources (header, cookie, query param).

    Args:
        request: FastAPI Request object

    Returns:
        Authenticated admin User object

    Raises:
        HTTPException 401: If not authenticated
        HTTPException 403: If authenticated but not admin
    """
    token = await get_token_from_request(request)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - no valid token found",
        )

    # Get database session maker
    from parade_state.db import get_session_maker

    session_maker = get_session_maker()
    if not session_maker:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection error",
        )

    async with session_maker() as db:
        session = await get_valid_session(db, token, update_last_accessed=True)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session token",
            )

        result = await db.execute(select(User).where(User.id == session.user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        if user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User account is {user.status}",
            )

        if user.role not in ["admin", "super_admin"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        return user