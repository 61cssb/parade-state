"""Tests for deployment API endpoints."""

import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.deployment import Deployment
from parade_state.models.schemas import DeploymentCreate, DeploymentUpdate
from parade_state.utils import utc_dt


@pytest.mark.asyncio
async def test_create_deployment_as_admin(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test deployment creation by admin."""
    deployment_data = {
        "name": "Test Deployment",
        "estab_id": "test-estab-123",
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
        "status": "draft",
        "notes": "Test deployment notes",
    }

    response = client.post(
        "/api/v1/deployments/",
        json=deployment_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Deployment"
    assert data["status"] == "draft"
    assert data["estab_id"] == "test-estab-123"


@pytest.mark.asyncio
async def test_create_deployment_as_regular_user_forbidden(
    client: TestClient, user_token_headers: dict[str, str], db_session
):
    """Test that regular users cannot create deployments."""
    deployment_data = {
        "name": "Test Deployment",
        "estab_id": "test-estab-123",
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
    }

    response = client.post(
        "/api/v1/deployments/",
        json=deployment_data,
        headers=user_token_headers,
        params={"user_id": "regular-user-id", "user_role": "user"},
    )

    assert response.status_code == 403
    assert "Only admins and super admins" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_deployment_invalid_date_range(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test deployment creation with invalid date range."""
    deployment_data = {
        "name": "Test Deployment",
        "estab_id": "test-estab-123",
        "valid_from": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
    }

    response = client.post(
        "/api/v1/deployments/",
        json=deployment_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "valid_until must be after valid_from" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_deployments(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test listing deployments."""
    # Create some test deployments
    deployment1 = Deployment(
        name="Deployment 1",
        estab_id="estab-1",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )
    deployment2 = Deployment(
        name="Deployment 2",
        estab_id="estab-2",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(deployment1)
    db_session.add(deployment2)
    await db_session.commit()

    response = client.get(
        "/api/v1/deployments/",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(d["name"] == "Deployment 1" for d in data)
    assert any(d["name"] == "Deployment 2" for d in data)


@pytest.mark.asyncio
async def test_list_deployments_with_status_filter(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test listing deployments with status filter."""
    # Create test deployments with different statuses
    deployment1 = Deployment(
        name="Active Deployment",
        estab_id="estab-1",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
        activated_at=utc_dt.utcnow(),
    )
    deployment2 = Deployment(
        name="Draft Deployment",
        estab_id="estab-2",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment1)
    db_session.add(deployment2)
    await db_session.commit()

    response = client.get(
        "/api/v1/deployments/",
        headers=admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
            "status": "active",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Active Deployment"
    assert data[0]["status"] == "active"


@pytest.mark.asyncio
async def test_get_deployment(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test getting a specific deployment."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    response = client.get(
        f"/api/v1/deployments/{deployment.id}",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(deployment.id)
    assert data["name"] == "Test Deployment"


@pytest.mark.asyncio
async def test_get_deployment_not_found(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test getting a non-existent deployment."""
    response = client.get(
        "/api/v1/deployments/non-existent-id",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_deployment(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test updating a deployment."""
    deployment = Deployment(
        name="Original Name",
        estab_id="estab-1",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    update_data = {"name": "Updated Name", "notes": "Updated notes"}

    response = client.patch(
        f"/api/v1/deployments/{deployment.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["notes"] == "Updated notes"


@pytest.mark.asyncio
async def test_update_deployment_status_transition(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test updating deployment status."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    update_data = {"status": "active"}

    response = client.patch(
        f"/api/v1/deployments/{deployment.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["activated_at"] is not None


@pytest.mark.asyncio
async def test_update_deployment_invalid_status_transition(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test updating deployment status with invalid transition."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="finalized",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    update_data = {"status": "active"}

    response = client.patch(
        f"/api/v1/deployments/{deployment.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Invalid status transition" in response.json()["detail"]


@pytest.mark.asyncio
async def test_activate_deployment(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test activating a deployment."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    response = client.post(
        f"/api/v1/deployments/{deployment.id}/activate",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["activated_at"] is not None


@pytest.mark.asyncio
async def test_activate_deployment_already_active(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test activating an already active deployment."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(deployment)
    await db_session.commit()

    response = client.post(
        f"/api/v1/deployments/{deployment.id}/activate",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "already active" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_deactivate_deployment(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test deactivating a deployment."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(deployment)
    await db_session.commit()

    response = client.post(
        f"/api/v1/deployments/{deployment.id}/deactivate",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "inactive"
    assert data["deactivated_at"] is not None


@pytest.mark.asyncio
async def test_delete_deployment_as_super_admin(
    client: TestClient, super_admin_token_headers: dict[str, str], db_session
):
    """Test deployment deletion by super admin."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="super-admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/deployments/{deployment.id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-user-id", "user_role": "super_admin"},
    )

    assert response.status_code == 204

    # Verify deployment was deleted
    result = await db_session.execute(
        select(Deployment).where(Deployment.id == deployment.id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_deployment_as_admin_forbidden(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test that regular admins cannot delete deployments."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/deployments/{deployment.id}",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 403
    assert "Only super admins" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_active_deployment_forbidden(
    client: TestClient, super_admin_token_headers: dict[str, str], db_session
):
    """Test that active deployments cannot be deleted."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="super-admin-user-id",
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(deployment)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/deployments/{deployment.id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-user-id", "user_role": "super_admin"},
    )

    assert response.status_code == 400
    assert "Cannot delete deployment" in response.json()["detail"]


@pytest.mark.asyncio
async def test_search_deployments(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test searching deployments by name."""
    deployment1 = Deployment(
        name="Alpha Deployment",
        estab_id="estab-1",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )
    deployment2 = Deployment(
        name="Bravo Deployment",
        estab_id="estab-2",
        status="draft",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment1)
    db_session.add(deployment2)
    await db_session.commit()

    response = client.get(
        "/api/v1/deployments/",
        headers=admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
            "search": "Alpha",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Alpha Deployment"


@pytest.mark.asyncio
async def test_list_deployments_pagination(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test deployment list pagination."""
    # Create multiple deployments
    for i in range(5):
        deployment = Deployment(
            name=f"Deployment {i}",
            estab_id=f"estab-{i}",
            status="draft",
            valid_from=utc_dt.utcnow(),
            valid_until=utc_dt.utcnow() + timedelta(days=30),
            created_by="admin-user-id",
        )
        db_session.add(deployment)

    await db_session.commit()

    # Get first page
    response = client.get(
        "/api/v1/deployments/",
        headers=admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
            "limit": 2,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    # Get second page
    response = client.get(
        "/api/v1/deployments/",
        headers=admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
            "limit": 2,
            "offset": 2,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
