"""Session management utilities."""

import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import UserSession
from parade_state.utils import utc_dt


def generate_session_token() -> str:
    """Generate a secure random session token."""
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
    """Create a new user session in the database."""
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
    """Get a valid session by token, optionally updating last accessed time."""
    result = await db.execute(select(UserSession).where(UserSession.token == token))
    session = result.scalar_one_or_none()

    if not session or not session.is_valid():
        return None

    if update_last_accessed:
        session.refresh_last_accessed()
        await db.commit()

    return session


async def invalidate_session(db: AsyncSession, token: str) -> bool:
    """Invalidate a session by deleting it."""
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
    """Invalidate all sessions for a user, optionally keeping one session."""
    # Build base delete statement
    stmt = delete(UserSession).where(UserSession.user_id == user_id)

    if except_token:
        stmt = stmt.where(UserSession.token != except_token)

    # Execute bulk delete
    result = await db.execute(stmt)
    await db.commit()

    return result.rowcount


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    """Clean up expired sessions from the database."""
    cutoff_time = utc_dt.ensure_naive(utc_dt.utcnow())
    stmt = delete(UserSession).where(UserSession.expires_at < cutoff_time)
    result = await db.execute(stmt)
    await db.commit()

    return result.rowcount
