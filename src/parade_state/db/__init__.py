"""Database configuration and session management."""

from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from parade_state.utils import ids


class Base(DeclarativeBase):
    """Base class for all database models."""

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=ids.db_default,
        index=True,
    )


# Global database engine and session factory
_engine = None
_async_session_maker = None
# Pool class the engine was built with, so runtime re-initialization
# (e.g. after a database restore swap) preserves the caller's choice
_poolclass = None


def normalize_database_url(database_url: str) -> str:
    """Normalize a DATABASE_URL for the async SQLAlchemy engine.

    Platform-provided Postgres URLs (Railway, Heroku) use the sync
    ``postgresql://`` or ``postgres://`` scheme, but ``create_async_engine``
    requires an explicit async driver. This translates such URLs to
    ``postgresql+asyncpg://`` in one place so neither the application
    lifespan nor the Alembic environment needs to care which form the
    platform injects.

    Query parameters are preserved. ``sslmode=`` (libpq spelling, used by
    e.g. Railway's public connection URL) is translated to ``ssl=``
    (asyncpg's connect argument spelling).

    Args:
        database_url: Raw database URL from the environment.

    Returns:
        URL usable by ``create_async_engine`` / Alembic's async engine.

    Example:
        ```python
        normalize_database_url("postgresql://u:p@host:5432/db")
        'postgresql+asyncpg://u:p@host:5432/db'

        normalize_database_url("postgresql://u:p@host:5432/db?sslmode=require")
        'postgresql+asyncpg://u:p@host:5432/db?ssl=require'
        ```
    """
    parts = urlsplit(database_url)
    scheme = parts.scheme.lower()
    query = parse_qsl(parts.query, keep_blank_values=True)

    # Only rewrite URLs that need it; everything else passes through
    # byte-identical (urlunsplit would otherwise collapse empty netlocs,
    # e.g. sqlite+aiosqlite:///:memory:)
    needs_scheme = scheme in ("postgresql", "postgres")
    needs_sslmode = any(key == "sslmode" for key, _ in query)
    if not needs_scheme and not needs_sslmode:
        return database_url

    if needs_scheme:
        scheme = "postgresql+asyncpg"
    query = [
        ("ssl", value) if key == "sslmode" else (key, value) for key, value in query
    ]
    return urlunsplit(parts._replace(scheme=scheme, query=urlencode(query)))


def init_database(database_url: str, poolclass: type | None = None) -> None:
    """Initialize the database engine and session factory.

    Args:
        database_url: Raw database URL from the environment.
        poolclass: Optional SQLAlchemy pool class. Asyncpg connections are
            bound to the event loop that created them, so contexts that run
            the engine across multiple loops (e.g. pytest fixtures plus
            FastAPI's TestClient portal) must pass ``NullPool`` to prevent
            cross-loop connection reuse.
    """
    global _engine, _async_session_maker, _poolclass

    _engine = create_async_engine(
        normalize_database_url(database_url),
        echo=False,  # Set to True for SQL logging in development
        pool_pre_ping=True,
        poolclass=poolclass,
    )
    _poolclass = poolclass

    _async_session_maker = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions."""
    if _async_session_maker is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    async with _async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


def get_session_maker() -> async_sessionmaker[AsyncSession] | None:
    """Get the async session maker for background tasks.

    Returns None if database has not been initialized yet.
    This allows the lifespan manager to check if initialization is needed.
    """
    return _async_session_maker
