"""Tests for API endpoints."""

import pytest
import uuid
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.main import app
from parade_state.models import User
from parade_state.session import create_user_session


@pytest.fixture
def client(db_session: AsyncSession):
    """Create test client with database session."""
    async def override_get_db():
        yield db_session

    from parade_state.db import get_db_session
    app.dependency_overrides[get_db_session] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


async def create_test_user_and_session(db_session: AsyncSession, role: str = "user", status: str = "active"):
    """Helper to create a test user and session."""
    # Generate unique email using UUID to avoid conflicts
    unique_id = str(uuid.uuid4())[:8]
    user = User(
        email=f"test_{role}_{status}_{unique_id}@example.com",
        name=f"Test {role} {status}",
        status=status,
        role=role,
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

    return user, session


@pytest.mark.asyncio
async def test_health_check(client: TestClient):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_get_current_user_info_unauthorized(client: TestClient):
    """Test that unauthorized requests to /me fail."""
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_info_authorized(client: TestClient, db_session: AsyncSession):
    """Test getting current user info with valid token."""
    user, session = await create_test_user_and_session(db_session)

    headers = {"Authorization": f"Bearer {session.token}"}
    response = client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == user.email
    assert data["name"] == user.name
    assert data["role"] == user.role


@pytest.mark.asyncio
async def test_logout_with_valid_token(client: TestClient, db_session: AsyncSession):
    """Test logout with valid token."""
    user, session = await create_test_user_and_session(db_session)

    headers = {"Authorization": f"Bearer {session.token}"}
    response = client.post("/api/v1/auth/logout", headers=headers)

    assert response.status_code == 200
    assert response.json()["message"] == "Logged out successfully"


@pytest.mark.asyncio
async def test_list_users_as_admin(client: TestClient, db_session: AsyncSession):
    """Test listing users as admin."""
    admin_user, admin_session = await create_test_user_and_session(db_session, role="admin")

    # Create some test users
    await create_test_user_and_session(db_session, role="user")
    await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {admin_session.token}"}
    response = client.get("/api/v1/users/", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert "users" in data
    assert len(data["users"]) >= 3  # At least the 3 users we created
    assert data["total_count"] >= 3


@pytest.mark.asyncio
async def test_list_users_as_regular_user(client: TestClient, db_session: AsyncSession):
    """Test that regular users cannot list users."""
    user, session = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {session.token}"}
    response = client.get("/api/v1/users/", headers=headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_user_as_admin(client: TestClient, db_session: AsyncSession):
    """Test getting a specific user as admin."""
    admin_user, admin_session = await create_test_user_and_session(db_session, role="admin")
    user, _ = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {admin_session.token}"}
    response = client.get(f"/api/v1/users/{user.id}", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == user.email
    assert data["name"] == user.name


@pytest.mark.asyncio
async def test_get_user_self(client: TestClient, db_session: AsyncSession):
    """Test that users can view their own profile."""
    user, session = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {session.token}"}
    response = client.get(f"/api/v1/users/{user.id}", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == user.email


@pytest.mark.asyncio
async def test_get_other_user_as_regular_user(client: TestClient, db_session: AsyncSession):
    """Test that regular users cannot view other users."""
    user1, session1 = await create_test_user_and_session(db_session, role="user")
    user2, _ = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {session1.token}"}
    response = client.get(f"/api/v1/users/{user2.id}", headers=headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_user_as_admin(client: TestClient, db_session: AsyncSession):
    """Test updating user as admin."""
    admin_user, admin_session = await create_test_user_and_session(db_session, role="admin")
    user, _ = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {admin_session.token}"}
    response = client.patch(
        f"/api/v1/users/{user.id}",
        json={"name": "Updated Name", "status": "active"},
        headers=headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_user_role_as_super_admin(client: TestClient, db_session: AsyncSession):
    """Test promoting user to admin as super admin."""
    super_admin, super_admin_session = await create_test_user_and_session(db_session, role="super_admin")
    user, _ = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {super_admin_session.token}"}
    response = client.patch(
        f"/api/v1/users/{user.id}",
        json={"role": "admin"},
        headers=headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "admin"


@pytest.mark.asyncio
async def test_update_user_role_as_regular_admin(client: TestClient, db_session: AsyncSession):
    """Test that regular admins cannot grant super admin role."""
    admin, admin_session = await create_test_user_and_session(db_session, role="admin")
    user, _ = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {admin_session.token}"}
    response = client.patch(
        f"/api/v1/users/{user.id}",
        json={"role": "super_admin"},
        headers=headers
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_user_as_super_admin(client: TestClient, db_session: AsyncSession):
    """Test deleting user as super admin."""
    super_admin, super_admin_session = await create_test_user_and_session(db_session, role="super_admin")
    user, _ = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {super_admin_session.token}"}
    response = client.delete(f"/api/v1/users/{user.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["message"] == "User deleted successfully"


@pytest.mark.asyncio
async def test_delete_user_as_regular_admin(client: TestClient, db_session: AsyncSession):
    """Test that regular admins cannot delete users."""
    admin, admin_session = await create_test_user_and_session(db_session, role="admin")
    user, _ = await create_test_user_and_session(db_session, role="user")

    headers = {"Authorization": f"Bearer {admin_session.token}"}
    response = client.delete(f"/api/v1/users/{user.id}", headers=headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_self_deletion_prevention(client: TestClient, db_session: AsyncSession):
    """Test that users cannot delete themselves."""
    super_admin, super_admin_session = await create_test_user_and_session(db_session, role="super_admin")

    headers = {"Authorization": f"Bearer {super_admin_session.token}"}
    response = client.delete(f"/api/v1/users/{super_admin.id}", headers=headers)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_user_filtering(client: TestClient, db_session: AsyncSession):
    """Test filtering users by status and role."""
    admin, admin_session = await create_test_user_and_session(db_session, role="admin")

    # Create users with different statuses and roles
    await create_test_user_and_session(db_session, role="user", status="active")
    await create_test_user_and_session(db_session, role="user", status="pending")
    await create_test_user_and_session(db_session, role="admin", status="active")

    headers = {"Authorization": f"Bearer {admin_session.token}"}

    # Filter by status
    response = client.get("/api/v1/users/?status_filter=active", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert all(user["status"] == "active" for user in data["users"])

    # Filter by role
    response = client.get("/api/v1/users/?role_filter=admin", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert all(user["role"] == "admin" for user in data["users"])


@pytest.mark.asyncio
async def test_user_search(client: TestClient, db_session: AsyncSession):
    """Test searching users by name or email."""
    admin, admin_session = await create_test_user_and_session(db_session, role="admin")

    # Create user with specific name
    user, _ = await create_test_user_and_session(db_session, role="user", status="active")

    headers = {"Authorization": f"Bearer {admin_session.token}"}

    # Search by email
    response = client.get(f"/api/v1/users/?search={user.email}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert any(u["email"] == user.email for u in data["users"])

    # Search by name
    response = client.get(f"/api/v1/users/?search={user.name}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert any(u["name"] == user.name for u in data["users"])


@pytest.mark.asyncio
async def test_pagination(client: TestClient, db_session: AsyncSession):
    """Test user list pagination."""
    admin, admin_session = await create_test_user_and_session(db_session, role="admin")

    # Create multiple users
    for i in range(5):
        await create_test_user_and_session(db_session, role="user", status="active")

    headers = {"Authorization": f"Bearer {admin_session.token}"}

    # Test pagination
    response = client.get("/api/v1/users/?skip=0&limit=2", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["users"]) <= 2
    assert data["skip"] == 0
    assert data["limit"] == 2