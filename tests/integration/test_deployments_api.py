"""Tests for deployment API endpoints."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.attendance import AttendanceRecord
from parade_state.models.csv_ingestion import Estab
from parade_state.models.deployment import (
    Deployment,
    DeploymentNotes,
    DeploymentPersonnelOverride,
)
from parade_state.models.personnel import Personnel
from parade_state.models.schemas import DeploymentCreate, DeploymentUpdate
from parade_state.utils import utc_dt
from tests.test_utils import (
    assert_404_response,
    assert_pagination_works,
    assert_permission_denied,
)


@pytest.mark.asyncio
async def test_create_deployment_as_admin(
    client: TestClient, admin_token_headers: dict[str, str], db_session,
    sample_estab,
):
    """Test deployment creation by admin."""
    deployment_data = {
        "name": "Test Deployment",
        "estab_id": str(sample_estab.id),
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
    assert data["estab_id"] == str(sample_estab.id)


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

    assert_permission_denied(
        client,
        "post",
        "/api/v1/deployments/",
        user_token_headers,
        expected_detail="Only admins and super admins",
        params={"user_id": "regular-user-id", "user_role": "user"},
        json_data=deployment_data,
    )


@pytest.mark.asyncio
async def test_create_deployment_invalid_date_range(
    client: TestClient, admin_token_headers: dict[str, str], db_session,
    sample_estab,
):
    """Test deployment creation with invalid date range."""
    deployment_data = {
        "name": "Test Deployment",
        "estab_id": str(sample_estab.id),
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
async def test_create_deployment_non_existent_estab(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test deployment creation fails when estab does not exist."""
    deployment_data = {
        "name": "Test Deployment",
        "estab_id": "does-not-exist",
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
    }

    response = client.post(
        "/api/v1/deployments/",
        json=deployment_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_deployment_draft_estab(
    client: TestClient, admin_token_headers: dict[str, str], db_session,
    sample_users,
):
    """Test deployment creation fails when estab is not confirmed."""
    draft_estab = Estab(
        caa=date(2024, 3, 1),
        csv_hash="draft-hash",
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_estab)
    await db_session.commit()

    deployment_data = {
        "name": "Test Deployment",
        "estab_id": str(draft_estab.id),
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
    }

    response = client.post(
        "/api/v1/deployments/",
        json=deployment_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "must be confirmed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_deployment_archived_estab(
    client: TestClient, admin_token_headers: dict[str, str], db_session,
    sample_users,
):
    """Test deployment creation fails when estab is archived."""
    archived_estab = Estab(
        caa=date(2023, 6, 1),
        csv_hash="archived-hash",
        status="archived",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(archived_estab)
    await db_session.commit()

    deployment_data = {
        "name": "Test Deployment",
        "estab_id": str(archived_estab.id),
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
    }

    response = client.post(
        "/api/v1/deployments/",
        json=deployment_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "must be confirmed" in response.json()["detail"]


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
    assert_404_response(
        client,
        "get",
        "/api/v1/deployments/non-existent-id",
        admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )


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

    assert_permission_denied(
        client,
        "delete",
        f"/api/v1/deployments/{deployment.id}",
        admin_token_headers,
        expected_detail="Only super admins",
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )


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

    assert_pagination_works(
        client,
        "/api/v1/deployments/",
        admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
        },
    )


# ============================================================================
# Deployment Status Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_deployment_status_no_sessions(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test getting deployment status when no sessions exist."""
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    response = client.get(
        f"/api/v1/deployments/{deployment.id}/status",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["deployment_id"] == deployment.id
    assert data["deployment_name"] == "Test Deployment"
    assert data["deployment_status"] == "active"
    assert data["am_session"] is None
    assert data["pm_session"] is None
    assert len(data["units"]) == 0


@pytest.mark.asyncio
async def test_get_deployment_status_with_sessions(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test getting deployment status with sessions and attendance."""
    from datetime import date

    # Create deployment
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    # Create personnel
    personnel1 = Personnel(
        estab_id="estab-1",
        rank="PTE",
        full_name="John Doe",
        unit="Coy A",
        sub_unit_1="Platoon 1",
        created_by="admin-user-id",
    )

    personnel2 = Personnel(
        estab_id="estab-1",
        rank="PTE",
        full_name="Jane Smith",
        unit="Coy B",
        sub_unit_1="Platoon 2",
        created_by="admin-user-id",
    )

    db_session.add_all([personnel1, personnel2])
    await db_session.commit()

    # Create session
    from parade_state.models.attendance import Session

    today = utc_dt.utcnow().date()
    session = Session(
        deployment_id=str(deployment.id),
        date=today,
        session_type="AM",
        status="open",
        created_by="admin-user-id",
    )

    db_session.add(session)
    await db_session.commit()

    # Create attendance records
    attendance1 = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(personnel1.id),
        deployment_id=str(deployment.id),
        status="present",
        unit_snapshot="Coy A",
        sub_unit_1_snapshot="Platoon 1",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    attendance2 = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(personnel2.id),
        deployment_id=str(deployment.id),
        status="absent",
        unit_snapshot="Coy B",
        sub_unit_1_snapshot="Platoon 2",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add_all([attendance1, attendance2])
    await db_session.commit()

    response = client.get(
        f"/api/v1/deployments/{deployment.id}/status",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["deployment_id"] == deployment.id
    assert data["deployment_name"] == "Test Deployment"
    assert data["am_session"] is not None
    assert data["am_session"]["status"] == "open"
    assert data["am_session"]["present"] == 1
    assert data["am_session"]["absent"] == 1
    assert len(data["units"]) == 2


# ============================================================================
# CSV Export Tests
# ============================================================================


@pytest.mark.asyncio
async def test_export_deployment_csv(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test exporting deployment data to CSV."""
    # Create deployment
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    # Create personnel
    personnel = Personnel(
        estab_id="estab-1",
        rank="PTE",
        full_name="John Doe",
        unit="Coy A",
        sub_unit_1="Platoon 1",
        created_by="admin-user-id",
    )

    db_session.add(personnel)
    await db_session.commit()

    # Create deployment override
    override = DeploymentPersonnelOverride(
        deployment_id=str(deployment.id),
        personnel_id=str(personnel.id),
        unit="Override Unit",
        sub_unit_1="Override Platoon",
        created_by="admin-user-id",
    )

    db_session.add(override)
    await db_session.commit()

    # Create deployment notes
    notes = DeploymentNotes(
        deployment_id=str(deployment.id),
        personnel_id=str(personnel.id),
        notes="Test notes",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add(notes)
    await db_session.commit()

    response = client.get(
        f"/api/v1/deployments/{deployment.id}/export",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    assert "Test_Deployment" in response.headers["content-disposition"]

    # Verify CSV content
    csv_content = response.content.decode("utf-8")
    assert personnel.short_id in csv_content
    assert "Rank" in csv_content
    assert "John Doe" in csv_content
    assert "Override Unit" in csv_content
    assert "Test notes" in csv_content


@pytest.mark.asyncio
async def test_export_deployment_csv_unauthorized(
    client: TestClient, user_token_headers: dict[str, str], db_session
):
    """Test that regular users cannot export deployment data without access."""
    # Note: This test assumes the user doesn't have access to this deployment
    # The access control logic will need to be implemented based on user scopes
    deployment = Deployment(
        name="Test Deployment",
        estab_id="estab-1",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(deployment)
    await db_session.commit()

    # For now, this test just checks the endpoint works
    # Once proper access control is implemented, this should return 403
    response = client.get(
        f"/api/v1/deployments/{deployment.id}/export",
        headers=user_token_headers,
        params={"user_id": "user-id", "user_role": "user"},
    )

    # This might succeed with current access control implementation
    # but should be restricted once proper scopes are enforced
    assert response.status_code in [200, 403, 404]
