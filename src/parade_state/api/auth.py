"""Authentication and user management endpoints."""

import os
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.responses import RedirectResponse

from parade_state.db import get_db_session
from parade_state.models import User
from parade_state.session import (
    create_user_session,
    get_valid_session,
    invalidate_session,
)
from parade_state.auth import get_oauth


router = APIRouter()
security = HTTPBearer()
oauth = get_oauth()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Get current authenticated user from session token."""
    token = credentials.credentials

    # Get valid session from database
    session = await get_valid_session(db, token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
        )

    # Get user from database
    result = await db.execute(select(User).where(User.id == uuid.UUID(session.user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Check if user is active
    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User account is {user.status}",
        )

    return user


@router.get("/login")
async def login():
    """Initiate Google OAuth login flow."""
    redirect_uri = os.getenv(
        "OAUTH_REDIRECT_URI",
        "http://localhost:8000/api/v1/auth/callback"
    )

    google = oauth.create_client("google")
    return await google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def auth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Handle Google OAuth callback and create user session."""
    try:
        google = oauth.create_client("google")
        token = await google.authorize_access_token(request)
        user_info = token.get("userinfo")

        if not user_info:
            user_info = await google.parse_id_token(request, token)

        email = user_info.get("email")
        name = user_info.get("name")
        google_id = user_info.get("sub")

        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not provided by Google OAuth",
            )

        # Check if user exists
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        # Check for super admin bootstrap
        super_admin_email = os.getenv("SUPER_ADMIN_EMAIL")

        if not user:
            # Auto-register user
            is_super_admin = super_admin_email == email

            user = User(
                email=email,
                name=name or email.split("@")[0],
                status="active" if is_super_admin else "pending",
                role="super_admin" if is_super_admin else "user",
                first_sign_in_at=datetime.utcnow(),
                last_sign_in_at=datetime.utcnow(),
            )

            db.add(user)
            await db.commit()
            await db.refresh(user)

        else:
            # Update last sign in
            user.last_sign_in_at = datetime.utcnow()

            # Update user info if changed
            if name:
                user.name = name

            await db.commit()

        # Check user status
        if user.status == "suspended":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account suspended",
            )

        # Create session
    user_session = await create_user_session(
        db,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )

    # Redirect to frontend with session token
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    redirect_url = f"{frontend_url}/auth/callback?token={user_session.token}"

    return RedirectResponse(url=redirect_url)

    except Exception as e:
        # Log error and return friendly message
        print(f"OAuth callback error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed",
        )


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
async def logout(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session),
):
    """Logout current user by invalidating session."""
    token = credentials.credentials
    await invalidate_session(db, token)

    return {"message": "Logged out successfully"}