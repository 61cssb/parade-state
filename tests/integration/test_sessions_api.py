"""Tests for session API endpoints."""

import pytest
from datetime import datetime, timedelta, date
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.attendance import Session
from parade_state.models.deployment import Deployment
from parade_state.utils import utc_dt
from tests.test_utils import assert_pagination_works, assert_404_response, assert_permission_denied


@pytest.mark.asyncio
async def test_create_session_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test session creation by admin for active deployment."""
    session_date = date.today()

    session_data = {
        "deployment_id": str(sample_deployment.id),
        "date": session_date.isoformat(),
        "session_type": "AM",
        "status": "open",
    }

    response = client.post(
        "/api/v1/sessions/",
        json=session_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["deployment_id"] == str(sample_deployment.id)
    assert data["session_type"] == "AM"
    assert data["status"] == "open"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_session_for_inactive_deployment_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_estab,
    sample_users,
):
    """Test that sessions cannot be created for inactive deployments."""
    # Create an inactive deployment
    deployment = Deployment(
        name="Inactive Deployment",
        estab_id=str(sample_estab.id),
        status="draft",  # Not active
        valid_from=utc_dt.utcnow() - timedelta(days=1),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        personnel_count=3,
        created_by=str(sample_users["admin"].id),
    )

    db_session.add(deployment)
    await db_session.commit()

    session_date = date.today()
    session_data = {
        "deployment_id": str(deployment.id),
        "date": session_date.isoformat(),
        "session_type": "AM",
    }

    response = client.post(
        "/api/v1/sessions/",
        json=session_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Sessions can only be created for active deployments" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_session_duplicate_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test that duplicate sessions (same deployment, date, type) are forbidden."""
    session_date = date.today()

    # Create first session
    session1 = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session1)
    await db_session.commit()

    # Try to create duplicate session
    session_data = {
        "deployment_id": str(sample_deployment.id),
        "date": session_date.isoformat(),
        "session_type": "AM",
    }

    response = client.post(
        "/api/v1/sessions/",
        json=session_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


async def test_create_session_as_regular_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_deployment: Deployment,
):
    """Test that regular users cannot create sessions."""
    session_date = date.today()

    session_data = {
        "deployment_id": str(sample_deployment.id),
        "date": session_date.isoformat(),
        "session_type": "PM",
    }

    assert_permission_denied(
        client,
        "post",
        "/api/v1/sessions/",
        user_token_headers,
        expected_detail="Only admins and super admins",
        params={"user_id": "regular-user-id", "user_role": "user"},
        json_data=session_data,
    )


async def test_create_session_deployment_not_found(
    client: TestClient,
    admin_token_headers: dict[str, str],
):
    """Test session creation with non-existent deployment."""
    session_date = date.today()

    session_data = {
        "deployment_id": "non-existent-deployment-id",
        "date": session_date.isoformat(),
        "session_type": "AM",
    }

    response = client.post(
        "/api/v1/sessions/",
        json=session_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_sessions(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test listing sessions."""
    session_date = date.today()

    # Create test sessions
    session1 = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    session2 = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="PM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add_all([session1, session2])
    await db_session.commit()

    response = client.get(
        "/api/v1/sessions/",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(s["session_type"] == "AM" for s in data)
    assert any(s["session_type"] == "PM" for s in data)


@pytest.mark.asyncio
async def test_list_sessions_with_deployment_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_estab,
    sample_users,
):
    """Test listing sessions filtered by deployment."""
    session_date = date.today()

    # Create another deployment
    deployment2 = Deployment(
        name="Deployment 2",
        estab_id=str(sample_estab.id),
        status="active",
        valid_from=utc_dt.utcnow() - timedelta(days=1),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        personnel_count=3,
        created_by=str(sample_users["admin"].id),
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(deployment2)
    await db_session.commit()

    # Create sessions for both deployments
    session1 = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    session2 = Session(
        deployment_id=str(deployment2.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add_all([session1, session2])
    await db_session.commit()

    # Filter by first deployment
    response = client.get(
        "/api/v1/sessions/",
        headers=admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
            "deployment_id": str(sample_deployment.id),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["deployment_id"] == str(sample_deployment.id)


@pytest.mark.asyncio
async def test_list_sessions_with_status_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test listing sessions filtered by status."""
    session_date = date.today()

    # Create sessions with different statuses
    session1 = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    session2 = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="PM",
        status="closed",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
        closed_at=utc_dt.utcnow(),
        closed_by="admin-user-id",
    )

    db_session.add_all([session1, session2])
    await db_session.commit()

    # Filter by open status
    response = client.get(
        "/api/v1/sessions/",
        headers=admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
            "status": "open",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "open"


@pytest.mark.asyncio
async def test_get_session(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test getting a specific session."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session)
    await db_session.commit()

    response = client.get(
        f"/api/v1/sessions/{session.id}",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(session.id)
    assert data["session_type"] == "AM"


async def test_get_session_not_found(
    client: TestClient,
    admin_token_headers: dict[str, str],
):
    """Test getting a non-existent session."""
    assert_404_response(
        client,
        "get",
        "/api/v1/sessions/non-existent-id",
        admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )


@pytest.mark.asyncio
async def test_update_session_status_to_closed(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test updating session status from open to closed."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session)
    await db_session.commit()

    update_data = {"status": "closed"}

    response = client.patch(
        f"/api/v1/sessions/{session.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "closed"
    assert data["closed_at"] is not None
    assert data["closed_by"] == "admin-user-id"


@pytest.mark.asyncio
async def test_update_session_status_to_finalized(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test updating session status from closed to finalized."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="closed",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
        closed_at=utc_dt.utcnow(),
        closed_by="admin-user-id",
    )

    db_session.add(session)
    await db_session.commit()

    update_data = {"status": "finalized"}

    response = client.patch(
        f"/api/v1/sessions/{session.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "finalized"


@pytest.mark.asyncio
async def test_update_session_invalid_status_transition(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test invalid status transition (open to finalized without closing)."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session)
    await db_session.commit()

    # Try to skip from open to finalized
    update_data = {"status": "finalized"}

    response = client.patch(
        f"/api/v1/sessions/{session.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Invalid status transition" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_finalized_session_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test that finalized sessions cannot be modified."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="finalized",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
        closed_at=utc_dt.utcnow(),
        closed_by="admin-user-id",
    )

    db_session.add(session)
    await db_session.commit()

    update_data = {"status": "closed"}

    response = client.patch(
        f"/api/v1/sessions/{session.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Cannot modify finalized sessions" in response.json()["detail"]


async def test_update_session_as_regular_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test that regular users cannot update sessions."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session)
    await db_session.commit()

    update_data = {"status": "closed"}

    assert_permission_denied(
        client,
        "patch",
        f"/api/v1/sessions/{session.id}",
        user_token_headers,
        expected_detail="Only admins and super admins",
        params={"user_id": "regular-user-id", "user_role": "user"},
        json_data=update_data,
    )


@pytest.mark.asyncio
async def test_delete_session_as_super_admin(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test session deletion by super admin."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/sessions/{session.id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-id", "user_role": "super_admin"},
    )

    assert response.status_code == 204

    # Verify session was deleted
    result = await db_session.execute(select(Session).where(Session.id == session.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_finalized_session_forbidden(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test that finalized sessions cannot be deleted."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="finalized",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
        closed_at=utc_dt.utcnow(),
        closed_by="admin-user-id",
    )

    db_session.add(session)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/sessions/{session.id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-id", "user_role": "super_admin"},
    )

    assert response.status_code == 400
    assert "Cannot delete finalized sessions" in response.json()["detail"]


async def test_delete_session_as_admin_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test that regular admins cannot delete sessions (only super admins)."""
    session_date = date.today()

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=session_date,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session)
    await db_session.commit()

    assert_permission_denied(
        client,
        "delete",
        f"/api/v1/sessions/{session.id}",
        admin_token_headers,
        expected_detail="Only super admins",
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )


@pytest.mark.asyncio
async def test_create_both_am_and_pm_sessions_for_same_day(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test that both AM and PM sessions can be created for the same day."""
    session_date = date.today()

    # Create AM session
    am_session_data = {
        "deployment_id": str(sample_deployment.id),
        "date": session_date.isoformat(),
        "session_type": "AM",
        "status": "open",
    }

    am_response = client.post(
        "/api/v1/sessions/",
        json=am_session_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert am_response.status_code == 201

    # Create PM session
    pm_session_data = {
        "deployment_id": str(sample_deployment.id),
        "date": session_date.isoformat(),
        "session_type": "PM",
        "status": "open",
    }

    pm_response = client.post(
        "/api/v1/sessions/",
        json=pm_session_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert pm_response.status_code == 201

    # Verify both sessions exist
    response = client.get(
        "/api/v1/sessions/",
        headers=admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
            "deployment_id": str(sample_deployment.id),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(s["session_type"] == "AM" for s in data)
    assert any(s["session_type"] == "PM" for s in data)


async def test_list_sessions_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test session list pagination."""
    base_date = date.today()

    # Create multiple sessions with different dates to avoid unique constraint
    sessions = []
    for i in range(5):
        session = Session(
            deployment_id=str(sample_deployment.id),
            date=base_date - timedelta(days=i),  # Different dates
            session_type="AM",
            status="open",
            created_by="admin-user-id",
            opened_at=utc_dt.utcnow(),
        )
        sessions.append(session)

    db_session.add_all(sessions)
    await db_session.commit()

    assert_pagination_works(
        client,
        "/api/v1/sessions/",
        admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
        },
    )


@pytest.mark.asyncio
async def test_session_auto_sets_opened_at(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
):
    """Test that session creation automatically sets opened_at timestamp."""
    session_date = date.today()

    session_data = {
        "deployment_id": str(sample_deployment.id),
        "date": session_date.isoformat(),
        "session_type": "AM",
        "status": "open",
    }

    before_creation = utc_dt.utcnow()

    response = client.post(
        "/api/v1/sessions/",
        json=session_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()

    # Verify opened_at is set and recent
    assert data["opened_at"] is not None

    # Parse the opened_at timestamp (naive, as stored in DB)
    opened_at = datetime.fromisoformat(data["opened_at"])

    # Verify it's close to current time (within 1 minute)
    # Use naive UTC time for comparison since opened_at is naive
    time_diff = abs((utc_dt.ensure_naive(utc_dt.utcnow()) - opened_at).total_seconds())
    assert time_diff < 60  # Less than 1 minute difference