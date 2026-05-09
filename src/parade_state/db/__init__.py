"""Database configuration and session management."""

from typing import AsyncGenerator

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


def init_database(database_url: str) -> None:
    """Initialize the database engine and session factory."""
    global _engine, _async_session_maker

    _engine = create_async_engine(
        database_url,
        echo=False,  # Set to True for SQL logging in development
        pool_pre_ping=True,
    )

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


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Get the async session maker for background tasks."""
    if _async_session_maker is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _async_session_maker
