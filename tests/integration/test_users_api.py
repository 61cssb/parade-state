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
