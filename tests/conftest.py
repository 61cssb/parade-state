"""Test configuration and fixtures."""

import asyncio
import uuid
from datetime import date, datetime, timedelta
from typing import AsyncGenerator

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from parade_state.db import Base, get_session_maker, init_database
from parade_state.main import app
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
from parade_state.utils import utc_dt


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_db():
    """Create a fresh test database engine for each test."""
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
    estab = Estab(
        caa=date(2024, 1, 1),
        csv_hash="dummy_hash",
        status="confirmed",
        personnel_count=3,
        uploaded_by=str(sample_users["admin"].id),
        confirmed_by=str(sample_users["admin"].id),
        confirmed_at=utc_dt.utcnow(),
    )

    db_session.add(estab)
    await db_session.commit()

    return estab


@pytest.fixture
async def sample_personnel(db_session: AsyncSession, sample_estab, sample_users):
    """Create sample personnel for testing."""
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
    admin_id = str(sample_users["admin"].id)
    estab_id = str(sample_estab.id)

    deployment = Deployment(
        name="Test Deployment",
        estab_id=estab_id,
        status="active",
        valid_from=utc_dt.utcnow() - timedelta(days=1),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        personnel_count=3,
        created_by=admin_id,
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(deployment)
    await db_session.commit()

    # Grant admin access to this deployment for testing
    admin_access = DeploymentUserAccess(
        user_id=admin_id,
        deployment_id=str(deployment.id),
        granted_by=admin_id,
    )
    db_session.add(admin_access)
    await db_session.commit()

    return deployment


@pytest.fixture
async def async_client(test_db):
    """Provide an async HTTP client for testing API endpoints."""
    from parade_state.main import app
    from parade_state.db import get_db_session

    # Override the database dependency
    async def override_get_db_session():
        async with test_db() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    # Clean up
    app.dependency_overrides.clear()


@pytest.fixture
def admin_token_headers(sample_users) -> dict[str, str]:
    """Provide headers with admin authentication token."""
    admin_id = str(sample_users["admin"].id)
    return {"Authorization": f"Bearer {admin_id}"}


@pytest.fixture
def user_token_headers(sample_users) -> dict[str, str]:
    """Provide headers with regular user authentication token."""
    user_id = str(sample_users["user"].id)
    return {"Authorization": f"Bearer {user_id}"}


@pytest.fixture
def super_admin_token_headers() -> dict[str, str]:
    """Provide headers with super admin authentication token."""
    super_admin_id = "super-admin-test-id"
    return {"Authorization": f"Bearer {super_admin_id}"}


@pytest.fixture
async def sample_session(db_session: AsyncSession, sample_deployment: Deployment, sample_users):
    """Create a sample session for testing."""
    admin_id = str(sample_users["admin"].id)

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=date.today(),
        session_type="AM",
        status="open",
        created_by=admin_id,
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session)
    await db_session.commit()

    return session


@pytest.fixture
async def sample_sessions(db_session: AsyncSession, sample_deployment: Deployment, sample_users):
    """Create multiple sample sessions for testing."""
    admin_id = str(sample_users["admin"].id)

    sessions = [
        Session(
            deployment_id=str(sample_deployment.id),
            date=date.today(),
            session_type="AM",
            status="closed",
            created_by=admin_id,
            opened_at=utc_dt.utcnow() - timedelta(hours=4),
            closed_at=utc_dt.utcnow() - timedelta(hours=2),
            closed_by=admin_id,
        ),
        Session(
            deployment_id=str(sample_deployment.id),
            date=date.today(),
            session_type="PM",
            status="open",
            created_by=admin_id,
            opened_at=utc_dt.utcnow(),
        ),
        Session(
            deployment_id=str(sample_deployment.id),
            date=date.today() - timedelta(days=1),
            session_type="AM",
            status="finalized",
            created_by=admin_id,
            opened_at=utc_dt.utcnow() - timedelta(days=1, hours=4),
            closed_at=utc_dt.utcnow() - timedelta(days=1, hours=2),
            closed_by=admin_id,
        ),
    ]

    for session in sessions:
        db_session.add(session)
    await db_session.commit()

    return sessions


@pytest.fixture
async def sample_attendance_records(
    db_session: AsyncSession,
    sample_sessions,
    sample_personnel,
    sample_deployment: Deployment,
    sample_users,
):
    """Create sample attendance records for testing."""
    admin_id = str(sample_users["admin"].id)

    # Create attendance records for the first personnel
    attendance_records = [
        # Today AM - present
        AttendanceRecord(
            session_id=str(sample_sessions[0].id),
            personnel_id=str(sample_personnel[0].id),
            deployment_id=str(sample_deployment.id),
            status="present",
            created_by=admin_id,
            updated_by=admin_id,
        ),
        # Today PM - absent
        AttendanceRecord(
            session_id=str(sample_sessions[1].id),
            personnel_id=str(sample_personnel[0].id),
            deployment_id=str(sample_deployment.id),
            status="absent",
            remarks="Sick leave",
            created_by=admin_id,
            updated_by=admin_id,
        ),
        # Yesterday AM - excused
        AttendanceRecord(
            session_id=str(sample_sessions[2].id),
            personnel_id=str(sample_personnel[0].id),
            deployment_id=str(sample_deployment.id),
            status="excused",
            remarks="Official duty",
            created_by=admin_id,
            updated_by=admin_id,
        ),
    ]

    for record in attendance_records:
        db_session.add(record)
    await db_session.commit()

    return attendance_records