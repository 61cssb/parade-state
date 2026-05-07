"""Test configuration and fixtures."""

import asyncio
import uuid
from datetime import date, datetime, timedelta
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from parade_state.db import Base, get_session_maker, init_database
from parade_state.models import (
    AccessLevel,
    AttendanceRecord,
    AuditLog,
    ColumnMapping,
    ColumnMetadata,
    CsvUpload,
    Deployment,
    DeploymentNotes,
    DeploymentPersonnelOverride,
    DeploymentUserAccess,
    Estab,
    Personnel,
    Session,
    User,
    UserSubunitScope,
)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_db():
    """Create a test database engine and initialize it."""
    # Use in-memory SQLite for tests (with async support)
    database_url = "sqlite+aiosqlite:///:memory:"

    engine = create_async_engine(database_url, echo=False)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

    # Initialize the global database state
    init_database(database_url)

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_session_maker

    # Cleanup
    await engine.dispose()


@pytest.fixture
async def db_session(test_db) -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for each test."""
    session = test_db()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()


@pytest.fixture
async def sample_access_levels(db_session: AsyncSession):
    """Create sample access levels for testing."""
    # Check if access levels already exist
    from sqlalchemy import select

    stmt = select(AccessLevel).where(AccessLevel.name.in_(["unit", "coy", "platoon", "section"]))
    result = await db_session.execute(stmt)
    existing_levels = result.scalars().all()

    if existing_levels:
        return {level.name: level for level in existing_levels}

    levels = [
        AccessLevel(name="unit", level_order=1),
        AccessLevel(name="coy", level_order=2),
        AccessLevel(name="platoon", level_order=3),
        AccessLevel(name="section", level_order=4),
    ]

    for level in levels:
        db_session.add(level)
    await db_session.commit()

    return {level.name: level for level in levels}


@pytest.fixture
async def sample_users(db_session: AsyncSession, sample_access_levels):
    """Create sample users for testing."""
    # Check if users already exist
    from sqlalchemy import select

    stmt = select(User).where(User.email.in_(["admin@example.com", "user@example.com"]))
    result = await db_session.execute(stmt)
    existing_users = result.scalars().all()

    if existing_users:
        return {user.email.split("@")[0]: user for user in existing_users}

    admin_user = User(
        email="admin@example.com",
        name="Admin User",
        role="admin",
        status="active",
        access_level_id=str(sample_access_levels["unit"].id),
    )

    regular_user = User(
        email="user@example.com",
        name="Regular User",
        role="user",
        status="active",
        access_level_id=str(sample_access_levels["platoon"].id),
    )

    db_session.add_all([admin_user, regular_user])
    await db_session.commit()

    return {"admin": admin_user, "user": regular_user}


@pytest.fixture
async def sample_estab(db_session: AsyncSession, sample_users):
    """Create a sample establishment for testing."""
    # Check if estab already exists
    from sqlalchemy import select

    stmt = select(Estab).where(Estab.caa == date(2024, 1, 1))
    result = await db_session.execute(stmt)
    existing_estab = result.scalar_one_or_none()

    if existing_estab:
        return existing_estab

    estab = Estab(
        caa=date(2024, 1, 1),
        csv_hash="dummy_hash",
        status="confirmed",
        personnel_count=3,
        uploaded_by=str(sample_users["admin"].id),
        confirmed_by=str(sample_users["admin"].id),
        confirmed_at=datetime.utcnow(),
    )

    db_session.add(estab)
    await db_session.commit()

    return estab


@pytest.fixture
async def sample_personnel(db_session: AsyncSession, sample_estab, sample_users):
    """Create sample personnel for testing."""
    # Check if personnel already exist
    from sqlalchemy import select

    stmt = select(Personnel).where(
        Personnel.pers_no.in_(["12345", "67890", "11111"]),
        Personnel.estab_id == str(sample_estab.id),
    )
    result = await db_session.execute(stmt)
    existing_personnel = result.scalars().all()

    if existing_personnel:
        return list(existing_personnel)

    admin_id = str(sample_users["admin"].id)
    estab_id = str(sample_estab.id)

    personnel = [
        Personnel(
            estab_id=estab_id,
            pers_no="12345",
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            sub_unit_1="Platoon 1",
            sub_unit_2="Section 1",
            created_by=admin_id,
        ),
        Personnel(
            estab_id=estab_id,
            pers_no="67890",
            rank="CPL",
            full_name="Jane Smith",
            unit="Coy A",
            sub_unit_1="Platoon 1",
            sub_unit_2="Section 2",
            created_by=admin_id,
        ),
        Personnel(
            estab_id=estab_id,
            pers_no="11111",
            rank="SGT",
            full_name="Bob Johnson",
            unit="Coy A",
            sub_unit_1="Platoon 2",
            sub_unit_2="Section 1",
            created_by=admin_id,
        ),
    ]

    for person in personnel:
        db_session.add(person)
    await db_session.commit()

    return personnel


@pytest.fixture
async def sample_deployment(db_session: AsyncSession, sample_estab, sample_users):
    """Create a sample deployment for testing."""
    # Check if deployment already exists
    from sqlalchemy import select

    stmt = select(Deployment).where(Deployment.name == "Test Deployment")
    result = await db_session.execute(stmt)
    existing_deployment = result.scalar_one_or_none()

    if existing_deployment:
        return existing_deployment

    admin_id = str(sample_users["admin"].id)
    estab_id = str(sample_estab.id)

    deployment = Deployment(
        name="Test Deployment",
        estab_id=estab_id,
        status="active",
        valid_from=datetime.utcnow() - timedelta(days=1),
        valid_until=datetime.utcnow() + timedelta(days=30),
        personnel_count=3,
        created_by=admin_id,
        activated_at=datetime.utcnow(),
    )

    db_session.add(deployment)
    await db_session.commit()

    return deployment