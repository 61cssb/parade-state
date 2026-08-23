"""Optional authentication dependencies for page routes.

This module provides the return-None-instead-of-raising variants pages
need (login/no-access redirects), resolving tokens from the Authorization
header or the session cookie — the same two sources as the strict
dependencies in ``auth/dependencies.py``. The ``?token=`` query-param
source was removed post-issue-31: tokens must never travel in URLs
(request logs, browser history, Referer headers).
"""

from fastapi import Request
from sqlalchemy import select

from parade_state.auth.session import get_valid_session
from parade_state.models import User
from parade_state.utils import cookies


async def get_token_from_request(request: Request) -> str | None:
    """Extract the session token from the request.

    Tries, in order:
    1. Authorization header (Bearer token)
    2. Cookie (session_token)

    Args:
        request: FastAPI Request object

    Returns:
        Token string if found, None otherwise
    """
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    return cookies.get_auth_token(request)


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


async def get_current_user_optional(
    request: Request,
) -> User | None:
    """Get current authenticated user from session without requiring admin role.

    Identical to get_current_admin_user_optional() but allows any active
    authenticated user (no role check). Used by non-admin views like
    grouping summary and attendance marking.

    Args:
        request: FastAPI Request object

    Returns:
        User object if authenticated and active, None otherwise
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

        if user and user.status == "active":
            return user

    return None


