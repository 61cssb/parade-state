"""Tests for audit log API endpoints."""

import pytest
from fastapi.testclient import TestClient

from parade_state.models import AuditLog


@pytest.mark.asyncio
async def test_list_audit_logs_empty(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
):
    """Test listing audit logs when none exist."""
    response = client.get(
        "/api/v1/audit/logs",
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["limit"] == 50
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_list_audit_logs_after_csv_upload(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test that CSV upload creates an audit entry visible in the list."""
    csv_content = b"rank,name\nPTE,John\n"

    upload_response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("test.csv", csv_content, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )
    assert upload_response.status_code == 200

    response = client.get(
        "/api/v1/audit/logs",
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1

    csv_entry = next(
        item for item in data["items"] if item["entity_type"] == "csv_upload"
    )
    assert csv_entry["action"] == "create"
    assert csv_entry["user_name"] == "Admin User"
    assert csv_entry["user_email"] == "admin@example.com"


@pytest.mark.asyncio
async def test_list_audit_logs_filter_by_entity_type(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test filtering audit logs by entity_type."""
    db_session.add_all(
        [
            AuditLog(
                user_id=admin_id,
                entity_type="user",
                entity_id="ent-1",
                action="update",
                description="Updated user",
            ),
            AuditLog(
                user_id=admin_id,
                entity_type="csv_upload",
                entity_id="ent-2",
                action="create",
                description="Uploaded CSV",
            ),
        ]
    )
    await db_session.commit()

    response = client.get(
        "/api/v1/audit/logs",
        params={
            "user_id": admin_id,
            "user_role": "admin",
            "entity_type": "user",
        },
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert all(item["entity_type"] == "user" for item in data["items"])


@pytest.mark.asyncio
async def test_list_audit_logs_filter_by_action(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test filtering audit logs by action."""
    db_session.add_all(
        [
            AuditLog(
                user_id=admin_id,
                entity_type="user",
                entity_id="ent-1",
                action="delete",
                description="Deleted user",
            ),
            AuditLog(
                user_id=admin_id,
                entity_type="user",
                entity_id="ent-2",
                action="update",
                description="Updated user",
            ),
        ]
    )
    await db_session.commit()

    response = client.get(
        "/api/v1/audit/logs",
        params={
            "user_id": admin_id,
            "user_role": "admin",
            "action": "delete",
        },
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert all(item["action"] == "delete" for item in data["items"])


@pytest.mark.asyncio
async def test_list_audit_logs_filter_by_target_user_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    sample_users,
    db_session,
):
    """Test filtering audit logs to a specific user's actions."""
    user_id = str(sample_users["user"].id)

    db_session.add_all(
        [
            AuditLog(
                user_id=admin_id,
                entity_type="user",
                entity_id="ent-1",
                action="update",
                description="Admin action",
            ),
            AuditLog(
                user_id=user_id,
                entity_type="user",
                entity_id="ent-2",
                action="update",
                description="Regular user action",
            ),
        ]
    )
    await db_session.commit()

    response = client.get(
        "/api/v1/audit/logs",
        params={
            "user_id": admin_id,
            "user_role": "admin",
            "target_user_id": user_id,
        },
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["user_id"] == user_id
    assert data["items"][0]["user_name"] == "Regular User"


@pytest.mark.asyncio
async def test_list_audit_logs_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test pagination of audit logs."""
    for i in range(5):
        db_session.add(
            AuditLog(
                user_id=admin_id,
                entity_type="user",
                entity_id=f"ent-{i}",
                action="update",
                description=f"Action {i}",
            )
        )
    await db_session.commit()

    response = client.get(
        "/api/v1/audit/logs",
        params={
            "user_id": admin_id,
            "user_role": "admin",
            "limit": 2,
            "offset": 0,
        },
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["total"] == 5

    # Next page
    response2 = client.get(
        "/api/v1/audit/logs",
        params={
            "user_id": admin_id,
            "user_role": "admin",
            "limit": 2,
            "offset": 4,
        },
        headers=admin_token_headers,
    )
    assert response2.status_code == 200
    assert len(response2.json()["items"]) == 1


@pytest.mark.asyncio
async def test_list_audit_logs_default_ordering(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test that entries are returned newest-first by timestamp."""
    from parade_state.utils import utc_dt

    older = AuditLog(
        user_id=admin_id,
        entity_type="user",
        entity_id="ent-old",
        action="create",
        description="Older entry",
    )
    db_session.add(older)
    await db_session.flush()

    # Force a later timestamp
    newer = AuditLog(
        user_id=admin_id,
        entity_type="user",
        entity_id="ent-new",
        action="create",
        description="Newer entry",
    )
    newer.timestamp = utc_dt.ensure_naive(utc_dt.utcnow())
    db_session.add(newer)
    await db_session.commit()

    response = client.get(
        "/api/v1/audit/logs",
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["items"][0]["description"] == "Newer entry"


@pytest.mark.asyncio
async def test_list_audit_logs_permission_denied(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_users,
):
    """Test that regular users cannot view audit logs."""
    user_id = str(sample_users["user"].id)

    response = client.get(
        "/api/v1/audit/logs",
        params={"user_id": user_id, "user_role": "user"},
        headers=user_token_headers,
    )

    assert response.status_code == 403
    assert "Only admins" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_audit_logs_null_user_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test that system entries (user_id=None) don't break the join."""
    db_session.add(
        AuditLog(
            user_id=None,
            entity_type="session",
            entity_id="ent-sys",
            action="close",
            description="System-triggered close",
        )
    )
    await db_session.commit()

    response = client.get(
        "/api/v1/audit/logs",
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    sys_entry = next(
        item for item in data["items"] if item["entity_id"] == "ent-sys"
    )
    assert sys_entry["user_id"] is None
    assert sys_entry["user_name"] is None
    assert sys_entry["user_email"] is None


@pytest.mark.asyncio
async def test_list_audit_logs_includes_user_name(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test that user_name and user_email are resolved via the User join."""
    db_session.add(
        AuditLog(
            user_id=admin_id,
            entity_type="user",
            entity_id="ent-x",
            action="update",
            description="Admin updated something",
        )
    )
    await db_session.commit()

    response = client.get(
        "/api/v1/audit/logs",
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    entry = data["items"][0]
    assert entry["user_name"] == "Admin User"
    assert entry["user_email"] == "admin@example.com"
