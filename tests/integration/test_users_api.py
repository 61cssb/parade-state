"""Tests for user management API audit log entries."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.auth.session import create_user_session
from parade_state.models import AuditLog, User


async def create_test_user_and_session(
    test_db, role: str = "user", status: str = "active"
):
    """Helper to create a test user and session."""
    unique_id = str(uuid.uuid4())[:8]
    user = User(
        email=f"test_{role}_{status}_{unique_id}@example.com",
        name=f"Test {role} {status}",
        status=status,
        role=role,
    )

    async with test_db() as db_session:
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
        await db_session.commit()

    return user, session


@pytest.mark.asyncio
async def test_update_user_creates_audit_log(client: TestClient, test_db):
    """Test that updating a user creates an AuditLog entry."""
    _, admin_session = await create_test_user_and_session(test_db, role="admin")
    user, _ = await create_test_user_and_session(test_db, role="user")

    headers = {"Authorization": f"Bearer {admin_session.token}"}
    response = client.patch(
        f"/api/v1/users/{user.id}",
        json={"name": "Updated Name", "role": "admin"},
        headers=headers,
    )

    assert response.status_code == 200

    # Verify AuditLog entry was created
    async with test_db() as db:
        result = await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "user",
                AuditLog.entity_id == str(user.id),
                AuditLog.action == "update",
            )
        )
        audit_log = result.scalar_one()
        assert "Updated Name" in audit_log.description
        assert "role" in audit_log.description


@pytest.mark.asyncio
async def test_update_user_status_creates_audit_log(client: TestClient, test_db):
    """Test that updating user status creates an AuditLog entry."""
    _, admin_session = await create_test_user_and_session(test_db, role="admin")
    user, _ = await create_test_user_and_session(test_db, role="user")

    headers = {"Authorization": f"Bearer {admin_session.token}"}
    response = client.patch(
        f"/api/v1/users/{user.id}",
        json={"status": "suspended"},
        headers=headers,
    )

    assert response.status_code == 200

    async with test_db() as db:
        result = await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "user",
                AuditLog.entity_id == str(user.id),
                AuditLog.action == "update",
            )
        )
        audit_log = result.scalar_one()
        assert "status" in audit_log.description
        assert "suspended" in audit_log.description


@pytest.mark.asyncio
async def test_delete_user_creates_audit_log(client: TestClient, test_db):
    """Test that deleting a user creates an AuditLog entry."""
    _, super_admin_session = await create_test_user_and_session(
        test_db, role="super_admin"
    )
    user, _ = await create_test_user_and_session(test_db, role="user")
    user_email = user.email
    user_id = str(user.id)

    headers = {"Authorization": f"Bearer {super_admin_session.token}"}
    response = client.delete(f"/api/v1/users/{user_id}", headers=headers)

    assert response.status_code == 200

    # Verify AuditLog entry was created
    async with test_db() as db:
        result = await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "user",
                AuditLog.entity_id == user_id,
                AuditLog.action == "delete",
            )
        )
        audit_log = result.scalar_one()
        assert user_email in audit_log.description


@pytest.mark.asyncio
async def test_create_user_pre_provisions_active_account(client: TestClient, test_db):
    """Super admin can create a user before first sign-in; account is active."""
    _, super_admin_session = await create_test_user_and_session(
        test_db, role="super_admin"
    )
    headers = {"Authorization": f"Bearer {super_admin_session.token}"}

    response = client.post(
        "/api/v1/users/",
        json={"email": "New.Admin@Example.com", "name": "New Admin", "role": "admin"},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.admin@example.com"  # normalized for OAuth match
    assert body["status"] == "active"
    assert body["role"] == "admin"

    async with test_db() as db:
        result = await db.execute(select(User).where(User.id == body["id"]))
        created = result.scalar_one()
        assert created.status == "active"
        assert created.first_sign_in_at is None

        audit_result = await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "user",
                AuditLog.entity_id == body["id"],
                AuditLog.action == "create",
            )
        )
        audit_log = audit_result.scalar_one()
        assert "new.admin@example.com" in audit_log.description


@pytest.mark.asyncio
async def test_create_user_defaults_name_from_email(client: TestClient, test_db):
    """Name falls back to the email local part when not provided."""
    _, super_admin_session = await create_test_user_and_session(
        test_db, role="super_admin"
    )
    headers = {"Authorization": f"Bearer {super_admin_session.token}"}

    response = client.post(
        "/api/v1/users/",
        json={"email": "nameless@example.com"},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["name"] == "nameless"


@pytest.mark.asyncio
async def test_create_user_forbidden_for_plain_admin(client: TestClient, test_db):
    """Only super admins can create users."""
    _, admin_session = await create_test_user_and_session(test_db, role="admin")
    headers = {"Authorization": f"Bearer {admin_session.token}"}

    response = client.post(
        "/api/v1/users/",
        json={"email": "someone@example.com"},
        headers=headers,
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_email(client: TestClient, test_db):
    """Duplicate emails (case-insensitive) are rejected to avoid OAuth mismatches."""
    existing, super_admin_session = await create_test_user_and_session(
        test_db, role="super_admin"
    )
    headers = {"Authorization": f"Bearer {super_admin_session.token}"}

    exact = client.post(
        "/api/v1/users/", json={"email": existing.email}, headers=headers
    )
    upper = client.post(
        "/api/v1/users/",
        json={"email": existing.email.upper()},
        headers=headers,
    )

    assert exact.status_code == 409
    assert upper.status_code == 409


@pytest.mark.asyncio
async def test_create_user_rejects_invalid_input(client: TestClient, test_db):
    """Malformed emails and unknown roles are rejected."""
    _, super_admin_session = await create_test_user_and_session(
        test_db, role="super_admin"
    )
    headers = {"Authorization": f"Bearer {super_admin_session.token}"}

    bad_email = client.post(
        "/api/v1/users/", json={"email": "not-an-email"}, headers=headers
    )
    bad_role = client.post(
        "/api/v1/users/",
        json={"email": "ok@example.com", "role": "wizard"},
        headers=headers,
    )

    assert bad_email.status_code == 400
    assert bad_role.status_code == 400


@pytest.mark.asyncio
async def test_promote_unrecognised_user_activates_account(client: TestClient, test_db):
    """Promoting an unrecognised user to an admin role also activates them."""
    _, super_admin_session = await create_test_user_and_session(
        test_db, role="super_admin"
    )
    user, _ = await create_test_user_and_session(
        test_db, role="user", status="unrecognised"
    )

    headers = {"Authorization": f"Bearer {super_admin_session.token}"}
    response = client.patch(
        f"/api/v1/users/{user.id}",
        json={"role": "super_admin"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["role"] == "super_admin"
    assert response.json()["status"] == "active"

    async with test_db() as db:
        result = await db.execute(select(User).where(User.id == user.id))
        updated = result.scalar_one()
        assert updated.status == "active"


@pytest.mark.asyncio
async def test_promote_suspended_user_keeps_suspension(client: TestClient, test_db):
    """Promoting a suspended user does not silently reactivate them."""
    _, super_admin_session = await create_test_user_and_session(
        test_db, role="super_admin"
    )
    user, _ = await create_test_user_and_session(
        test_db, role="user", status="suspended"
    )

    headers = {"Authorization": f"Bearer {super_admin_session.token}"}
    response = client.patch(
        f"/api/v1/users/{user.id}",
        json={"role": "admin"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "suspended"
