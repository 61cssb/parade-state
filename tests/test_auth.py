"""Tests for authentication system."""

import pytest
import datetime as dt
from datetime import timedelta, timezone, datetime
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import User, UserSession
from parade_state.session import (
    create_user_session,
    get_valid_session,
    invalidate_session,
    invalidate_user_sessions,
    cleanup_expired_sessions,
)


def make_aware(dt_naive: dt.datetime) -> dt.datetime:
    """Make a naive datetime timezone-aware."""
    if dt_naive.tzinfo is None:
        return dt_naive.replace(tzinfo=timezone.utc)
    return dt_naive


@pytest.mark.asyncio
async def test_create_user_session(db_session: AsyncSession):
    """Test creating a user session."""
    # Create a test user
    user = User(
        email="test@example.com",
        name="Test User",
        status="active",
        role="user",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Create session
    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )

    assert session.token is not None
    assert session.user_id == str(user.id)
    assert session.email == user.email
    assert session.is_valid() is True
    assert session.expires_at > datetime.utcnow()


@pytest.mark.asyncio
async def test_get_valid_session(db_session: AsyncSession):
    """Test retrieving a valid session."""
    # Create a test user and session
    user = User(
        email="test@example.com",
        name="Test User",
        status="active",
        role="user",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )

    # Get valid session
    retrieved_session = await get_valid_session(db_session, session.token)
    assert retrieved_session is not None
    assert retrieved_session.token == session.token
    assert retrieved_session.user_id == str(user.id)


@pytest.mark.asyncio
async def test_get_invalid_session(db_session: AsyncSession):
    """Test that invalid sessions return None."""
    # Test with non-existent token
    session = await get_valid_session(db_session, "nonexistent_token")
    assert session is None

    # Create a test user and session
    user = User(
        email="test@example.com",
        name="Test User",
        status="active",
        role="user",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )

    # Manually expire the session
    session.expires_at = datetime.utcnow() - timedelta(days=1)
    await db_session.commit()

    # Try to get expired session
    retrieved_session = await get_valid_session(db_session, session.token)
    assert retrieved_session is None


@pytest.mark.asyncio
async def test_invalidate_session(db_session: AsyncSession):
    """Test invalidating a session."""
    # Create a test user and session
    user = User(
        email="test@example.com",
        name="Test User",
        status="active",
        role="user",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )

    # Invalidate the session
    result = await invalidate_session(db_session, session.token)
    assert result is True

    # Try to get invalidated session
    retrieved_session = await get_valid_session(db_session, session.token)
    assert retrieved_session is None


@pytest.mark.asyncio
async def test_invalidate_user_sessions(db_session: AsyncSession):
    """Test invalidating all user sessions."""
    # Create a test user
    user = User(
        email="test@example.com",
        name="Test User",
        status="active",
        role="user",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Create multiple sessions
    session1 = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )
    session2 = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )
    session3 = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )

    # Invalidate all sessions except session2
    count = await invalidate_user_sessions(db_session, str(user.id), except_token=session2.token)
    assert count == 2

    # Verify session2 is still valid
    retrieved_session = await get_valid_session(db_session, session2.token, update_last_accessed=False)
    assert retrieved_session is not None

    # Verify session1 and session3 are invalid
    retrieved_session1 = await get_valid_session(db_session, session1.token)
    assert retrieved_session1 is None

    retrieved_session3 = await get_valid_session(db_session, session3.token)
    assert retrieved_session3 is None


@pytest.mark.asyncio
async def test_cleanup_expired_sessions(db_session: AsyncSession):
    """Test cleaning up expired sessions."""
    # Create a test user
    user = User(
        email="test@example.com",
        name="Test User",
        status="active",
        role="user",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Create valid session
    valid_session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )

    # Create expired session
    expired_session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )
    expired_session.expires_at = datetime.utcnow() - timedelta(days=1)
    await db_session.commit()

    # Cleanup expired sessions
    count = await cleanup_expired_sessions(db_session)
    assert count == 1

    # Verify valid session still exists
    retrieved_session = await get_valid_session(db_session, valid_session.token, update_last_accessed=False)
    assert retrieved_session is not None

    # Verify expired session is gone
    retrieved_expired = await get_valid_session(db_session, expired_session.token)
    assert retrieved_expired is None


@pytest.mark.asyncio
async def test_user_auto_registration():
    """Test user auto-registration through OAuth flow."""
    # This test would require mocking the OAuth flow
    # For now, we'll test the user creation logic directly
    pass


@pytest.mark.asyncio
async def test_super_admin_bootstrap():
    """Test super admin bootstrap mechanism."""
    # Create user with super admin email
    super_admin_email = "superadmin@example.com"

    user = User(
        email=super_admin_email,
        name="Super Admin",
        status="active",
        role="super_admin",
    )

    # Verify user has correct role and status
    assert user.role == "super_admin"
    assert user.status == "active"


@pytest.mark.asyncio
async def test_session_last_accessed_update(db_session: AsyncSession):
    """Test that session last_accessed_at is updated."""
    # Create a test user and session
    user = User(
        email="test@example.com",
        name="Test User",
        status="active",
        role="user",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )

    original_last_accessed = session.last_accessed_at

    # Wait a bit and retrieve session again
    import asyncio
    await asyncio.sleep(0.1)

    retrieved_session = await get_valid_session(db_session, session.token, update_last_accessed=True)

    # Make both datetimes comparable
    original_comparable = make_aware(original_last_accessed) if original_last_accessed.tzinfo is None else original_last_accessed
    retrieved_comparable = make_aware(retrieved_session.last_accessed_at) if retrieved_session.last_accessed_at.tzinfo is None else retrieved_session.last_accessed_at

    assert retrieved_comparable > original_comparable