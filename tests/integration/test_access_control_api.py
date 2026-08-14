"""Tests for access control management API endpoints."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from parade_state.models import Grouping, GroupingUserAccess, User, UserSubunitScope
from tests.test_utils import assert_permission_denied


@pytest.mark.asyncio
async def test_grant_user_grouping_access_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
):
    """Test that admins can grant grouping access to users."""
    target_user_id = str(sample_users["user"].id)

    response = client.post(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/access",
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
    assert data["grouping_id"] == str(sample_grouping.id)
    assert data["granted_by"] == str(sample_users["admin"].id)
    assert "granted_at" in data
    assert data["revoked_at"] is None


@pytest.mark.asyncio
async def test_grant_user_grouping_access_as_super_admin(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
):
    """Test that super admins can grant grouping access."""
    target_user_id = str(sample_users["user"].id)

    response = client.post(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/access",
        headers=super_admin_token_headers,
        params={
            "granted_by": "super-admin-id",
            "user_role": "super_admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["user_id"] == target_user_id
    assert data["grouping_id"] == str(sample_grouping.id)


@pytest.mark.asyncio
async def test_grant_user_grouping_access_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
):
    """Test that regular users cannot grant grouping access."""
    target_user_id = str(sample_users["user"].id)

    assert_permission_denied(
        client,
        "post",
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/access",
        user_token_headers,
        expected_detail="Only admins can grant grouping access",
        params={
            "granted_by": str(sample_users["user"].id),
            "user_role": "user",
        },
    )


@pytest.mark.asyncio
async def test_grant_duplicate_grouping_access(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test that duplicate grouping access grants are rejected."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Create first access grant
    first_access = GroupingUserAccess(
        user_id=target_user_id,
        grouping_id=str(sample_grouping.id),
        granted_by=admin_id,
    )
    db_session.add(first_access)
    await db_session.commit()

    # Try to create duplicate access grant
    response = client.post(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/access",
        headers=admin_token_headers,
        params={
            "granted_by": admin_id,
            "user_role": "admin",
        },
    )

    assert response.status_code == 400
    assert "already has access to this grouping" in response.json()["detail"]


@pytest.mark.asyncio
async def test_revoke_user_grouping_access(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test revoking user grouping access."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Create access grant first
    access = GroupingUserAccess(
        user_id=target_user_id,
        grouping_id=str(sample_grouping.id),
        granted_by=admin_id,
    )
    db_session.add(access)
    await db_session.commit()

    # Revoke access
    response = client.delete(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/access",
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
    sample_grouping: Grouping,
    sample_users,
):
    """Test revoking non-existent access."""
    target_user_id = str(sample_users["user"].id)

    response = client.delete(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/access",
        headers=admin_token_headers,
        params={
            "revoked_by": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 404
    assert "User access not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_user_grouping_accesses_own(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test user can list their own grouping accesses."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Create access grants
    access1 = GroupingUserAccess(
        user_id=target_user_id,
        grouping_id=str(sample_grouping.id),
        granted_by=admin_id,
    )
    db_session.add(access1)
    await db_session.commit()

    # List own accesses
    response = client.get(
        f"/api/v1/access-control/users/{target_user_id}/groupings",
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
    assert first_access["grouping_id"] == str(sample_grouping.id)


@pytest.mark.asyncio
async def test_list_other_user_accesses_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test admin can list other users' grouping accesses."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Create access grant
    access = GroupingUserAccess(
        user_id=target_user_id,
        grouping_id=str(sample_grouping.id),
        granted_by=admin_id,
    )
    db_session.add(access)
    await db_session.commit()

    # List user's accesses as admin
    response = client.get(
        f"/api/v1/access-control/users/{target_user_id}/groupings",
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
    sample_grouping: Grouping,
    sample_users,
):
    """Test regular users cannot list other users' grouping accesses."""
    admin_user_id = str(sample_users["admin"].id)
    regular_user_id = str(sample_users["user"].id)

    assert_permission_denied(
        client,
        "get",
        f"/api/v1/access-control/users/{admin_user_id}/groupings",
        user_token_headers,
        expected_detail="can only view your own",
        params={
            "requesting_user_id": regular_user_id,
            "requesting_user_role": "user",
        },
    )


@pytest.mark.asyncio
async def test_list_grouping_users(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test listing users with access to a grouping."""
    admin_id = str(sample_users["admin"].id)
    user_id = str(sample_users["user"].id)

    # Grant user access to grouping
    # Note: Admin access is already granted by sample_grouping fixture
    user_access = GroupingUserAccess(
        user_id=user_id,
        grouping_id=str(sample_grouping.id),
        granted_by=admin_id,
    )
    db_session.add(user_access)
    await db_session.commit()

    # List grouping users
    response = client.get(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users",
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
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test creating a subunit scope for a user."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Note: Admin access already granted by sample_grouping fixture
    response = client.post(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/scopes",
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
    assert data["grouping_id"] == str(sample_grouping.id)
    assert data["unit"] == "Coy A"
    assert data["sub_unit_1"] == "Platoon 1"
    assert data["sub_unit_2"] == "Section 1"
    assert data["sub_unit_3"] is None


@pytest.mark.asyncio
async def test_create_duplicate_subunit_scope(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test that duplicate subunit scopes are rejected."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Note: Admin access already granted by sample_grouping fixture
    # Create first scope
    scope = UserSubunitScope(
        user_id=target_user_id,
        grouping_id=str(sample_grouping.id),
        unit="Coy A",
        sub_unit_1="Platoon 1",
        created_by=admin_id,
    )
    db_session.add(scope)
    await db_session.commit()

    # Try to create duplicate scope
    response = client.post(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/scopes",
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
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test deleting a user subunit scope."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Note: Admin access already granted by sample_grouping fixture
    # Create scope
    scope = UserSubunitScope(
        user_id=target_user_id,
        grouping_id=str(sample_grouping.id),
        unit="Coy A",
        created_by=admin_id,
    )
    db_session.add(scope)
    await db_session.commit()
    scope_id = str(scope.id)

    # Delete scope
    response = client.delete(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/scopes/{scope_id}",
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
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test listing user subunit scopes."""
    target_user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Note: Admin access already granted by sample_grouping fixture
    # Create scopes
    scope1 = UserSubunitScope(
        user_id=target_user_id,
        grouping_id=str(sample_grouping.id),
        unit="Coy A",
        sub_unit_1="Platoon 1",
        created_by=admin_id,
    )
    scope2 = UserSubunitScope(
        user_id=target_user_id,
        grouping_id=str(sample_grouping.id),
        unit="Coy A",
        sub_unit_1="Platoon 2",
        created_by=admin_id,
    )
    db_session.add_all([scope1, scope2])
    await db_session.commit()

    # List scopes
    response = client.get(
        f"/api/v1/access-control/groupings/{sample_grouping.id}/users/{target_user_id}/scopes",
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
async def test_access_control_enforcement_grouping_access(
    client: TestClient,
    user_token_headers: dict[str, str],
    admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
    db_session,
):
    """Test that users without grouping access are blocked."""
    user_id = str(sample_users["user"].id)

    # Try to access grouping personnel without access grant
    response = client.get(
        "/api/v1/personnel",
        headers=user_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": user_id,
            "user_role": "user",
        },
    )

    # Should be blocked (403) because user doesn't have grouping access
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_access_control_with_grouping_access_grant(
    client: TestClient,
    user_token_headers: dict[str, str],
    admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
    sample_users,
    sample_personnel,
    db_session,
):
    """Test that users with grouping access can access grouping data."""
    user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)

    # Grant user access to grouping
    access = GroupingUserAccess(
        user_id=user_id,
        grouping_id=str(sample_grouping.id),
        granted_by=admin_id,
    )
    db_session.add(access)
    await db_session.commit()

    # Try to access grouping personnel with access grant
    response = client.get(
        "/api/v1/personnel",
        headers=user_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": user_id,
            "user_role": "user",
        },
    )

    # Should succeed now (200) because user has grouping access
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_super_admin_full_access(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    sample_grouping: Grouping,
):
    """Test that super admins have full access without explicit grants."""
    response = client.get(
        "/api/v1/personnel",
        headers=super_admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": "super-admin-id",
            "user_role": "super_admin",
        },
    )

    # Super admins should have access
    assert response.status_code == 200
