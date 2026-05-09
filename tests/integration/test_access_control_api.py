"""Tests for access control management API endpoints."""

import pytest
from datetime import datetime
from fastapi.testclient import TestClient

from parade_state.models import User, Deployment, DeploymentUserAccess, UserSubunitScope


@pytest.mark.asyncio
async def test_grant_user_deployment_access_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
):
    """Test that admins can grant deployment access to users."""
    target_user_id = str(sample_users["user"].id)

    response = client.post(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/access",
        headers=admin_token_headers,
        params={
            "granted_by": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "id" in data
    assert data["user_id"] == target_user_id
    assert data["deployment_id"] == str(sample_deployment.id)
    assert data["granted_by"] == str(sample_users["admin"].id)
    assert "granted_at" in data
    assert data["revoked_at"] is None


@pytest.mark.asyncio
async def test_grant_user_deployment_access_as_super_admin(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
):
    """Test that super admins can grant deployment access."""
    target_user_id = str(sample_users["user"].id)

    response = client.post(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/access",
        headers=super_admin_token_headers,
        params={
            "granted_by": "super-admin-id",
            "user_role": "super_admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["user_id"] == target_user_id
    assert data["deployment_id"] == str(sample_deployment.id)


@pytest.mark.asyncio
async def test_grant_user_deployment_access_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
):
    """Test that regular users cannot grant deployment access."""
    target_user_id = str(sample_users["user"].id)

    response = client.post(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/access",
        headers=user_token_headers,
        params={
            "granted_by": str(sample_users["user"].id),
            "user_role": "user",
        },
    )

    assert response.status_code == 403
    assert "Only admins can grant deployment access" in response.json()["detail"]


@pytest.mark.asyncio
async def test_grant_duplicate_deployment_access(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test that duplicate deployment access grants are rejected."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Create first access grant
    first_access = DeploymentUserAccess(
        user_id=target_user_id,
        deployment_id=str(sample_deployment.id),
        granted_by=admin_id,
    )
    db_session.add(first_access)
    await db_session.commit()

    # Try to create duplicate access grant
    response = client.post(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/access",
        headers=admin_token_headers,
        params={
            "granted_by": admin_id,
            "user_role": "admin",
        },
    )

    assert response.status_code == 400
    assert "already has access to this deployment" in response.json()["detail"]


@pytest.mark.asyncio
async def test_revoke_user_deployment_access(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test revoking user deployment access."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Create access grant first
    access = DeploymentUserAccess(
        user_id=target_user_id,
        deployment_id=str(sample_deployment.id),
        granted_by=admin_id,
    )
    db_session.add(access)
    await db_session.commit()

    # Revoke access
    response = client.delete(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/access",
        headers=admin_token_headers,
        params={
            "revoked_by": admin_id,
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    assert "revoked successfully" in response.json()["message"]


@pytest.mark.asyncio
async def test_revoke_nonexistent_access(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
):
    """Test revoking non-existent access."""
    target_user_id = str(sample_users["user"].id)

    response = client.delete(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/access",
        headers=admin_token_headers,
        params={
            "revoked_by": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 404
    assert "User access not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_user_deployment_accesses_own(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test user can list their own deployment accesses."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Create access grants
    access1 = DeploymentUserAccess(
        user_id=target_user_id,
        deployment_id=str(sample_deployment.id),
        granted_by=admin_id,
    )
    db_session.add(access1)
    await db_session.commit()

    # List own accesses
    response = client.get(
        f"/api/v1/access-control/users/{target_user_id}/deployments",
        headers=user_token_headers,
        params={
            "requesting_user_id": target_user_id,
            "requesting_user_role": "user",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # Check first access
    first_access = data[0]
    assert first_access["user_id"] == target_user_id
    assert first_access["deployment_id"] == str(sample_deployment.id)


@pytest.mark.asyncio
async def test_list_other_user_accesses_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test admin can list other users' deployment accesses."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Create access grant
    access = DeploymentUserAccess(
        user_id=target_user_id,
        deployment_id=str(sample_deployment.id),
        granted_by=admin_id,
    )
    db_session.add(access)
    await db_session.commit()

    # List user's accesses as admin
    response = client.get(
        f"/api/v1/access-control/users/{target_user_id}/deployments",
        headers=admin_token_headers,
        params={
            "requesting_user_id": admin_id,
            "requesting_user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_list_other_user_accesses_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
):
    """Test regular users cannot list other users' deployment accesses."""
    admin_user_id = str(sample_users["admin"].id)
    regular_user_id = str(sample_users["user"].id)

    response = client.get(
        f"/api/v1/access-control/users/{admin_user_id}/deployments",
        headers=user_token_headers,
        params={
            "requesting_user_id": regular_user_id,
            "requesting_user_role": "user",
        },
    )

    assert response.status_code == 403
    assert "can only view your own" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_deployment_users(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test listing users with access to a deployment."""
    admin_id = str(sample_users["admin"].id)
    user_id = str(sample_users["user"].id)

    # Grant user access to deployment
    # Note: Admin access is already granted by sample_deployment fixture
    user_access = DeploymentUserAccess(
        user_id=user_id,
        deployment_id=str(sample_deployment.id),
        granted_by=admin_id,
    )
    db_session.add(user_access)
    await db_session.commit()

    # List deployment users
    response = client.get(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users",
        headers=admin_token_headers,
        params={
            "requesting_user_id": admin_id,
            "requesting_user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # Check user is in the list
    user_ids = [u["user_id"] for u in data]
    assert user_id in user_ids


@pytest.mark.asyncio
async def test_create_user_subunit_scope(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test creating a subunit scope for a user."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Note: Admin access already granted by sample_deployment fixture
    response = client.post(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/scopes",
        headers=admin_token_headers,
        params={
            "created_by": admin_id,
            "user_role": "admin",
        },
        json={
            "unit": "Coy A",
            "sub_unit_1": "Platoon 1",
            "sub_unit_2": "Section 1",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "id" in data
    assert data["user_id"] == target_user_id
    assert data["deployment_id"] == str(sample_deployment.id)
    assert data["unit"] == "Coy A"
    assert data["sub_unit_1"] == "Platoon 1"
    assert data["sub_unit_2"] == "Section 1"
    assert data["sub_unit_3"] is None


@pytest.mark.asyncio
async def test_create_duplicate_subunit_scope(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test that duplicate subunit scopes are rejected."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Note: Admin access already granted by sample_deployment fixture
    # Create first scope
    scope = UserSubunitScope(
        user_id=target_user_id,
        deployment_id=str(sample_deployment.id),
        unit="Coy A",
        sub_unit_1="Platoon 1",
        created_by=admin_id,
    )
    db_session.add(scope)
    await db_session.commit()

    # Try to create duplicate scope
    response = client.post(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/scopes",
        headers=admin_token_headers,
        params={
            "created_by": admin_id,
            "user_role": "admin",
        },
        json={
            "unit": "Coy A",
            "sub_unit_1": "Platoon 1",
        },
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_user_subunit_scope(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test deleting a user subunit scope."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Note: Admin access already granted by sample_deployment fixture
    # Create scope
    scope = UserSubunitScope(
        user_id=target_user_id,
        deployment_id=str(sample_deployment.id),
        unit="Coy A",
        created_by=admin_id,
    )
    db_session.add(scope)
    await db_session.commit()
    scope_id = str(scope.id)

    # Delete scope
    response = client.delete(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/scopes/{scope_id}",
        headers=admin_token_headers,
        params={
            "deleted_by": admin_id,
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    assert "deleted successfully" in response.json()["message"]


@pytest.mark.asyncio
async def test_list_user_subunit_scopes(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test listing user subunit scopes."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Note: Admin access already granted by sample_deployment fixture
    # Create scopes
    scope1 = UserSubunitScope(
        user_id=target_user_id,
        deployment_id=str(sample_deployment.id),
        unit="Coy A",
        sub_unit_1="Platoon 1",
        created_by=admin_id,
    )
    scope2 = UserSubunitScope(
        user_id=target_user_id,
        deployment_id=str(sample_deployment.id),
        unit="Coy A",
        sub_unit_1="Platoon 2",
        created_by=admin_id,
    )
    db_session.add_all([scope1, scope2])
    await db_session.commit()

    # List scopes
    response = client.get(
        f"/api/v1/access-control/deployments/{sample_deployment.id}/users/{target_user_id}/scopes",
        headers=admin_token_headers,
        params={
            "requesting_user_id": admin_id,
            "requesting_user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2

    # Check scopes
    units = [s["unit"] for s in data]
    assert all(u == "Coy A" for u in units)


@pytest.mark.asyncio
async def test_access_control_enforcement_deployment_access(
    client: TestClient,
    user_token_headers: dict[str, str],
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    db_session,
):
    """Test that users without deployment access are blocked."""
    user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Try to access deployment personnel without access grant
    response = client.get(
        "/api/v1/personnel",
        headers=user_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": user_id,
            "user_role": "user",
        },
    )

    # Should be blocked (403) because user doesn't have deployment access
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_access_control_with_deployment_access_grant(
    client: TestClient,
    user_token_headers: dict[str, str],
    admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_users,
    sample_personnel,
    db_session,
):
    """Test that users with deployment access can access deployment data."""
    user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Grant user access to deployment
    access = DeploymentUserAccess(
        user_id=user_id,
        deployment_id=str(sample_deployment.id),
        granted_by=admin_id,
    )
    db_session.add(access)
    await db_session.commit()

    # Try to access deployment personnel with access grant
    response = client.get(
        "/api/v1/personnel",
        headers=user_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": user_id,
            "user_role": "user",
        },
    )

    # Should succeed now (200) because user has deployment access
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_super_admin_full_access(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    sample_deployment: Deployment,
):
    """Test that super admins have full access without explicit grants."""
    response = client.get(
        "/api/v1/personnel",
        headers=super_admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": "super-admin-id",
            "user_role": "super_admin",
        },
    )

    # Super admins should have access
    assert response.status_code == 200
