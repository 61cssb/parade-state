"""Test configuration and fixtures."""

import asyncio
import uuid
from datetime import date, datetime, timedelta
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

# Ensure all models are imported so they're registered with Base
import parade_state.models.access  # noqa: F401
import parade_state.models.attendance  # noqa: F401
import parade_state.models.audit  # noqa: F401
import parade_state.models.auth_session  # noqa: F401
import parade_state.models.csv_ingestion  # noqa: F401
import parade_state.models.deferments  # noqa: F401
import parade_state.models.grouping  # noqa: F401
import parade_state.models.personnel  # noqa: F401
import parade_state.models.tagging  # noqa: F401
from parade_state.db import Base, get_session_maker, init_database
from parade_state.main import app
from parade_state.models import (
    AccessLevel,
    Attendance,
    AuditLog,
    ColumnMapping,
    ColumnMetadata,
    CsvUpload,
    Grouping,
    GroupingNotes,
    GroupingPersonnelExclusion,
    GroupingPersonnelOverride,
    GroupingUserAccess,
    NominalRoll,
    Personnel,
    User,
    UserSession,
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
async def test_engine(tmp_path):
    """Create test database engine for each test.

    This fixture is function-scoped to ensure complete test isolation.
    Each test gets its own database file and engine, preventing data
    leakage between tests.
    """
    # Create a unique database file for this test
    db_file = tmp_path / "test.db"
    database_url = f"sqlite+aiosqlite:///{db_file}"

    # Initialize the GLOBAL database state
    # This ensures get_db_session() returns the test database
    # Critical for authentication tests to work correctly
    init_database(database_url)

    # Get the global engine that was just created
    from parade_state.db import _engine

    engine = _engine

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Cleanup: dispose engine after test completes
    await engine.dispose()


@pytest.fixture
def session_maker(test_engine):
    """Create session maker for each test.

    This fixture is function-scoped to match the test_engine scope,
    ensuring each test uses its own session maker.
    """
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture
def test_db(session_maker):
    """Compatibility alias for session_maker.

    This fixture maintains backward compatibility with existing test code
    that uses the `test_db` parameter name. It simply returns the
    function-scoped session maker.

    TODO: Gradually migrate test code to use `session_maker` directly.
    """
    return session_maker


@pytest.fixture
async def db_session(session_maker) -> AsyncGenerator[AsyncSession, None]:
    """Provide a fresh database session for each test.

    Each test gets its own session that is properly rolled back
    and closed after the test completes.
    """
    session = session_maker()
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
async def sample_nominal_roll(db_session: AsyncSession, sample_users):
    """Create a sample nominal roll for testing."""
    nominal_roll = NominalRoll(
        caa=date(2024, 1, 1),
        csv_hash="dummy_hash",
        personnel_count=3,
        uploaded_by=str(sample_users["admin"].id),
    )

    db_session.add(nominal_roll)
    await db_session.commit()

    return nominal_roll


@pytest.fixture
async def sample_personnel(db_session: AsyncSession, sample_nominal_roll, sample_users):
    """Create sample personnel for testing."""
    admin_id = str(sample_users["admin"].id)
    nominal_roll_id = str(sample_nominal_roll.id)

    # pers_no values are numeric so they never collide with the p001/p002
    # pers_no values carried by the test CSV fixture (test_csv_process_api).
    personnel = [
        Personnel(
            nominal_roll_id=nominal_roll_id,
            pers_no="10000001",
            rank="PTE",
            category="WOSE",
            full_name="John Doe",
            unit="Coy A",
            sub_unit_1="Platoon 1",
            sub_unit_2="Section 1",
            created_by=admin_id,
        ),
        Personnel(
            nominal_roll_id=nominal_roll_id,
            pers_no="10000002",
            rank="CPL",
            category="WOSE",
            full_name="Jane Smith",
            unit="Coy A",
            sub_unit_1="Platoon 1",
            sub_unit_2="Section 2",
            created_by=admin_id,
        ),
        Personnel(
            nominal_roll_id=nominal_roll_id,
            pers_no="10000003",
            rank="CPT",
            category="Officer",
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
async def sample_grouping(db_session: AsyncSession, sample_nominal_roll, sample_users):
    """Create a sample grouping for testing."""
    admin_id = str(sample_users["admin"].id)
    nominal_roll_id = str(sample_nominal_roll.id)

    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id=nominal_roll_id,
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow() - timedelta(days=1),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        personnel_count=3,
        created_by=admin_id,
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(grouping)
    await db_session.commit()

    # Grant admin access to this grouping for testing
    admin_access = GroupingUserAccess(
        user_id=admin_id,
        grouping_id=str(grouping.id),
        granted_by=admin_id,
    )
    db_session.add(admin_access)
    await db_session.commit()

    return grouping


@pytest.fixture
def client(session_maker):
    """Provide a test client for API endpoints using FastAPI TestClient.

    This fixture creates a TestClient with the test database dependency
    overridden to use the function-scoped test database. Each test gets
    its own client with a fresh database.
    """
    from fastapi.testclient import TestClient

    from parade_state.db import get_db_session

    # Override the database dependency to use the test database
    async def override_get_db_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        # Clean up: remove dependency override
        app.dependency_overrides.clear()


@pytest.fixture
def admin_token_headers(sample_users) -> dict[str, str]:
    """Provide headers with admin authentication token."""
    admin_id = str(sample_users["admin"].id)
    return {"Authorization": f"Bearer {admin_id}"}


@pytest.fixture
def admin_id(sample_users) -> str:
    """Provide the admin user ID for test parameters."""
    return str(sample_users["admin"].id)


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
async def sample_attendance_scope(
    db_session: AsyncSession, sample_nominal_roll, sample_users
):
    """Mark the sample NR as the one active for attendance.

    Kept under the historical name ``sample_attendance_scope`` so existing
    test signatures keep working.
    """
    admin_id = str(sample_users["admin"].id)
    sample_nominal_roll.attendance_active = True
    sample_nominal_roll.attendance_activated_at = utc_dt.ensure_naive(
        utc_dt.utcnow()
    )
    sample_nominal_roll.attendance_activated_by = admin_id
    db_session.add(sample_nominal_roll)
    await db_session.commit()
    return sample_nominal_roll


@pytest.fixture
async def admin_subunit_assignment(
    db_session: AsyncSession, sample_nominal_roll, sample_personnel, sample_users
):
    """Grant the admin user Subunit-1 assignments covering the sample roster.

    Sample personnel span Platoon 1 (personnel 0, 1) and Platoon 2 (personnel 2).
    This lets attendance-mechanics tests exercise the happy path under the
    PR 2 deny-by-default gate without each test re-granting access.
    """
    from parade_state.models import UserSubunitAssignment

    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    for sub1 in {"Platoon 1", "Platoon 2"}:
        db_session.add(
            UserSubunitAssignment(
                user_id=admin_id,
                nominal_roll_id=nr_id,
                sub_unit_1=sub1,
                created_by=admin_id,
            )
        )
    await db_session.commit()
    return admin_id


@pytest.fixture
async def sample_attendance(
    db_session: AsyncSession,
    sample_personnel,
    sample_nominal_roll,
    sample_users,
):
    """Create sample attendance rows (AM/PM) for testing.

    Builds two days of history for the first personnel member:
    - Today: AM present, PM absent (remarks)
    - Yesterday: AM late (remarks), PM absent
    """
    admin_id = str(sample_users["admin"].id)
    nominal_roll_id = str(sample_nominal_roll.id)
    today = date.today()
    yesterday = today - timedelta(days=1)

    rows = [
        Attendance(
            personnel_id=str(sample_personnel[0].id),
            nominal_roll_id=nominal_roll_id,
            date=today,
            status_am="present",
            status_pm="absent",
            remarks_pm="Sick leave",
            created_by=admin_id,
            updated_by=admin_id,
        ),
        Attendance(
            personnel_id=str(sample_personnel[0].id),
            nominal_roll_id=nominal_roll_id,
            date=yesterday,
            status_am="late",
            remarks_am="Official duty",
            status_pm="absent",
            created_by=admin_id,
            updated_by=admin_id,
        ),
        Attendance(
            personnel_id=str(sample_personnel[1].id),
            nominal_roll_id=nominal_roll_id,
            date=today,
            status_am="present",
            status_pm="present",
            created_by=admin_id,
            updated_by=admin_id,
        ),
    ]

    for row in rows:
        db_session.add(row)
    await db_session.commit()

    return rows
