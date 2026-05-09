"""Session management utilities.

This module provides session management functionality for user authentication,
separated from API and web routing layers.

## Key Functions

### Session Creation
- `create_user_session()` - Create new session with token generation
- `generate_session_token()` - Generate secure random token

### Session Validation
- `get_valid_session()` - Validate and retrieve session by token
- `invalidate_session()` - Delete specific session
- `invalidate_user_sessions()` - Delete all user sessions

### Session Cleanup
- `cleanup_expired_sessions()` - Remove expired sessions from database

## Architecture Notes

This module is part of the core authentication logic and should be imported by:
- `parade_state.web.auth` - OAuth callback flows
- `parade_state.api.auth` - Logout endpoints
- `parade_state.auth.dependencies` - Session validation

It should NOT import from API or web modules to maintain clean separation.

## Session Lifecycle

```
1. User logs in via OAuth
   ↓
2. create_user_session() generates token and stores UserSession
   ↓
3. Client stores token and sends in Authorization header
   ↓
4. get_valid_session() validates token on each request
   ↓
5. Session expires after 7 days or is invalidated
```

## Security Features

- **Token Generation:** Uses `secrets.token_urlsafe(32)` for 256-bit security
- **Expiration:** Sessions expire after 7 days by default
- **Validation:** Every request checks database for valid session
- **Tracking:** Stores IP, user agent, and last accessed time
"""

import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import UserSession
from parade_state.utils import utc_dt


def generate_session_token() -> str:
    """Generate a secure random session token.

    Uses 256-bit random generation via secrets.token_urlsafe(32).

    Returns:
        URL-safe random token suitable for session identification
    """
    return secrets.token_urlsafe(32)


async def create_user_session(
    db: AsyncSession,
    user_id: str,
    email: str,
    name: str,
    role: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
    expires_days: int = 7,
) -> UserSession:
    """Create a new user session in the database.

    Generates a secure token and creates a UserSession record with
    expiration tracking and metadata.

    Args:
        db: Database session
        user_id: User ID to associate with session
        email: User email for session metadata
        name: User name for session metadata
        role: User role for session metadata
        user_agent: Client user agent string for security tracking
        ip_address: Client IP address for security tracking
        expires_days: Days until session expires (default: 7)

    Returns:
        Created UserSession object with generated token

    Example:
        ```python
        session = await create_user_session(
            db,
            user_id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
        token = session.token  # Send this to client
        ```
    """
    token = generate_session_token()
    expires_at = utc_dt.ensure_naive(
        utc_dt.add_timedelta(utc_dt.utcnow(), days=expires_days)
    )

    session = UserSession(
        token=token,
        user_id=user_id,
        email=email,
        name=name,
        role=role,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    db.add(session)
    await db.commit()
    await db.refresh(session)

    return session


async def get_valid_session(
    db: AsyncSession,
    token: str,
    update_last_accessed: bool = True,
) -> UserSession | None:
    """Get a valid session by token, optionally updating last accessed time.

    Validates that the session exists and has not expired.
    Optionally updates the last_accessed timestamp for activity tracking.

    Args:
        db: Database session
        token: Session token to validate
        update_last_accessed: Whether to update last_accessed timestamp

    Returns:
        UserSession if valid and not expired, None otherwise

    Example:
        ```python
        session = await get_valid_session(db, token)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid session")
        # session.user_id contains the authenticated user ID
        ```
    """
    result = await db.execute(select(UserSession).where(UserSession.token == token))
    session = result.scalar_one_or_none()

    if not session or not session.is_valid():
        return None

    if update_last_accessed:
        session.refresh_last_accessed()
        await db.commit()

    return session


async def invalidate_session(db: AsyncSession, token: str) -> bool:
    """Invalidate a session by deleting it.

    Used for logout functionality or security revocation.

    Args:
        db: Database session
        token: Session token to invalidate

    Returns:
        True if session was deleted, False if not found

    Example:
        ```python
        success = await invalidate_session(db, token)
        if not success:
            raise HTTPException(status_code=404, detail="Session not found")
        ```
    """
    result = await db.execute(select(UserSession).where(UserSession.token == token))
    session = result.scalar_one_or_none()

    if not session:
        return False

    await db.delete(session)
    await db.commit()
    return True


async def invalidate_user_sessions(
    db: AsyncSession,
    user_id: str,
    except_token: str | None = None,
) -> int:
    """Invalidate all sessions for a user, optionally keeping one session.

    Used for:
    - Password reset (invalidate all sessions)
    - Security events (force logout from all devices)
    - Session cleanup (keep current session, remove others)

    Args:
        db: Database session
        user_id: User ID whose sessions to invalidate
        except_token: If provided, keep this session valid

    Returns:
        Number of sessions deleted

    Example:
        ```python
        # Invalidate all sessions (force logout from all devices)
        count = await invalidate_user_sessions(db, user_id)

        # Keep current session, invalidate others
        count = await invalidate_user_sessions(db, user_id, except_token=current_token)
        ```
    """
    # Build base delete statement
    stmt = delete(UserSession).where(UserSession.user_id == user_id)

    if except_token:
        stmt = stmt.where(UserSession.token != except_token)

    # Execute bulk delete
    result = await db.execute(stmt)
    await db.commit()

    return result.rowcount


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    """Clean up expired sessions from the database.

    Should be run periodically to maintain database performance.
    Can be called from a scheduled job or admin endpoint.

    Args:
        db: Database session

    Returns:
        Number of expired sessions deleted

    Example:
        ```python
        # In a scheduled cleanup job
        deleted_count = await cleanup_expired_sessions(db)
        logger.info(f"Cleaned up {deleted_count} expired sessions")
        ```
    """
    cutoff_time = utc_dt.ensure_naive(utc_dt.utcnow())
    stmt = delete(UserSession).where(UserSession.expires_at < cutoff_time)
    result = await db.execute(stmt)
    await db.commit()

    return result.rowcount
