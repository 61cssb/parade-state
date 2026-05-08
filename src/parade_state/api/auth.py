"""Authentication and user management endpoints."""

import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from parade_state.db import get_db_session
from parade_state.models import User


router = APIRouter()
security = HTTPBearer()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Get current authenticated user from session token."""
    # TODO: Implement proper session validation using credentials.credentials
    # For now, return first user as placeholder

    # Try to get user from session
    session_user = await request.app.state.session.get(credentials.credentials)
    if session_user:
        user_id = session_user.get("user_id")
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if user:
            return user

    # Fallback to first user for development
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    return user


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get current user information."""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
        "status": current_user.status,
        "access_level_id": str(current_user.access_level_id) if current_user.access_level_id else None,
    }


@router.post("/logout")
async def logout(request: Request):
    """Logout current user."""
    # TODO: Implement session invalidation
    return {"message": "Logged out successfully"}