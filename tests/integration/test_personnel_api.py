"""Tests for personnel management API endpoints."""

import pytest
from datetime import date, datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.deployment import Deployment, DeploymentNotes, DeploymentPersonnelOverride
from parade_state.models.personnel import Personnel
from tests.test_utils import assert_pagination_works, assert_404_response, assert_permission_denied


@pytest.mark.asyncio
async def test_list_personnel_with_deployment_context(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
    sample_users,
):
    """Test listing personnel with deployment context."""
    admin_id = str(sample_users["admin"].id)

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": admin_id,
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check first personnel has deployment context
    first_personnel = data[0]
    assert "id" in first_personnel
    assert "name" in first_personnel
    assert "service_number" in first_personnel
    assert "deployment_id" in first_personnel
    assert first_personnel["deployment_id"] == str(sample_deployment.id)
    assert "has_override" in first_personnel
    assert "deployment_notes" in first_personnel


@pytest.mark.asyncio
async def test_list_personnel_without_deployment_context_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_personnel,
    sample_users,
):
    """Test listing personnel without deployment context as admin."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check personnel don't have deployment context
    first_personnel = data[0]
    assert "id" in first_personnel
    assert "name" in first_personnel
    assert first_personnel["deployment_id"] is None
    assert first_personnel["has_override"] is False
    assert first_personnel["deployment_notes"] is None


@pytest.mark.asyncio
async def test_list_personnel_without_deployment_context_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
):
    """Test that regular users cannot list personnel without deployment context."""
    assert_permission_denied(
        client,
        "get",
        "/api/v1/personnel",
        user_token_headers,
        expected_detail="Only admins can list personnel without deployment context",
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
    )


@pytest.mark.asyncio
async def test_list_personnel_with_unit_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test listing personnel with unit filter."""
    # Get the unit of first personnel
    first_unit = sample_personnel[0].unit

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "unit": first_unit,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)

    # All returned personnel should have the specified unit
    for personnel in data:
        assert personnel["unit"] == first_unit


@pytest.mark.asyncio
async def test_list_personnel_with_sub_unit_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test listing personnel with sub-unit filter."""
    # Get the sub_unit_1 of first personnel that has one
    sub_unit = None
    for p in sample_personnel:
        if p.sub_unit_1:
            sub_unit = p.sub_unit_1
            break

    if sub_unit:
        response = client.get(
            "/api/v1/personnel",
            headers=admin_token_headers,
            params={
                "deployment_id": str(sample_deployment.id),
                "sub_unit_1": sub_unit,
                "user_id": str(sample_users["admin"].id),
                "user_role": "admin",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

        # All returned personnel should have the specified sub_unit_1
        for personnel in data:
            assert personnel["sub_unit_1"] == sub_unit


@pytest.mark.asyncio
async def test_list_personnel_with_search(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test listing personnel with search functionality."""
    # Search for first personnel's name
    search_term = sample_personnel[0].full_name[:5]  # Use first 5 characters

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "search": search_term,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check that search term matches
    found = False
    for personnel in data:
        if search_term.lower() in personnel["name"].lower():
            found = True
            break
    assert found, "Search term should match at least one personnel"


@pytest.mark.asyncio
async def test_list_personnel_with_search_by_service_number(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test listing personnel with search by service number."""
    # Search for first personnel's service number
    search_term = sample_personnel[0].pers_no[:5]  # Use first 5 characters

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "search": search_term,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check that search term matches service number
    found = False
    for personnel in data:
        if search_term.lower() in personnel["service_number"].lower():
            found = True
            break
    assert found, "Search term should match at least one personnel service number"


@pytest.mark.asyncio
async def test_list_personnel_with_overrides(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
    sample_users,
):
    """Test listing personnel with deployment overrides."""
    # Create override for first personnel
    override = DeploymentPersonnelOverride(
        deployment_id=str(sample_deployment.id),
        personnel_id=str(sample_personnel[0].id),
        unit="Override Unit",
        sub_unit_1="Override Subunit",
        created_by=str(sample_users["admin"].id),
    )

    db_session.add(override)
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Find the personnel with override
    personnel_with_override = None
    for personnel in data:
        if personnel["id"] == str(sample_personnel[0].id):
            personnel_with_override = personnel
            break

    assert personnel_with_override is not None
    assert personnel_with_override["has_override"] is True


@pytest.mark.asyncio
async def test_list_personnel_with_deployment_notes(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
    sample_users,
):
    """Test listing personnel with deployment notes."""
    # Create deployment notes for first personnel
    notes = DeploymentNotes(
        deployment_id=str(sample_deployment.id),
        personnel_id=str(sample_personnel[0].id),
        notes="Medical exemption granted",
        created_by=str(sample_users["admin"].id),
        updated_by=str(sample_users["admin"].id),
    )

    db_session.add(notes)
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Find the personnel with notes
    personnel_with_notes = None
    for personnel in data:
        if personnel["id"] == str(sample_personnel[0].id):
            personnel_with_notes = personnel
            break

    assert personnel_with_notes is not None
    assert personnel_with_notes["deployment_notes"] == "Medical exemption granted"


@pytest.mark.asyncio
async def test_get_personnel_by_id_with_deployment_context(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test getting personnel by ID with deployment context."""
    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["name"] == sample_personnel[0].full_name
    assert data["deployment_id"] == str(sample_deployment.id)
    assert "has_override" in data
    assert "deployment_notes" in data


@pytest.mark.asyncio
async def test_get_personnel_by_id_without_deployment_context_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """Test getting personnel by ID without deployment context as admin."""
    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["name"] == sample_personnel[0].full_name
    assert data["deployment_id"] is None
    assert data["has_override"] is False
    assert data["deployment_notes"] is None


async def test_get_personnel_by_id_without_deployment_context_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_personnel,
):
    """Test that regular users cannot get personnel without deployment context."""
    assert_permission_denied(
        client,
        "get",
        f"/api/v1/personnel/{sample_personnel[0].id}",
        user_token_headers,
        expected_detail="Only admins can view personnel without deployment context",
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
    )


@pytest.mark.asyncio
async def test_get_personnel_by_id_with_override_and_notes(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
    sample_users,
):
    """Test getting personnel by ID with override and notes."""
    # Create override
    override = DeploymentPersonnelOverride(
        deployment_id=str(sample_deployment.id),
        personnel_id=str(sample_personnel[0].id),
        unit="Override Unit",
        sub_unit_1="Override Subunit",
        created_by=str(sample_users["admin"].id),
    )

    db_session.add(override)

    # Create notes
    notes = DeploymentNotes(
        deployment_id=str(sample_deployment.id),
        personnel_id=str(sample_personnel[0].id),
        notes="Medical exemption granted",
        created_by=str(sample_users["admin"].id),
        updated_by=str(sample_users["admin"].id),
    )

    db_session.add(notes)
    await db_session.commit()

    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["has_override"] is True
    assert data["deployment_notes"] == "Medical exemption granted"


@pytest.mark.asyncio
async def test_update_personnel_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test updating personnel as admin."""
    update_data = {
        "rank": "Updated Rank",
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["rank"] == "Updated Rank"


async def test_update_personnel_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_personnel,
):
    """Test that regular users cannot update personnel."""
    update_data = {
        "rank": "Updated Rank",
    }

    assert_permission_denied(
        client,
        "patch",
        f"/api/v1/personnel/{sample_personnel[0].id}",
        user_token_headers,
        expected_detail="Only admins can update personnel records",
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
        json_data=update_data,
    )


@pytest.mark.asyncio
async def test_update_personnel_status(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test updating personnel status."""
    update_data = {
        "status": "archived",
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["status"] == "archived"


@pytest.mark.asyncio
async def test_list_personnel_with_status_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test listing personnel with status filter."""
    # Archive first personnel
    sample_personnel[0].status = "archived"
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "status": "archived",
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # All returned personnel should have archived status
    for personnel in data:
        assert personnel["status"] == "archived"


async def test_list_personnel_with_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
):
    """Test listing personnel with pagination."""
    assert_pagination_works(
        client,
        "/api/v1/personnel",
        admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )


async def test_list_personnel_invalid_deployment_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Test listing personnel with invalid deployment ID."""
    assert_404_response(
        client,
        "get",
        "/api/v1/personnel",
        admin_token_headers,
        params={
            "deployment_id": "invalid-deployment-id",
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )


async def test_get_personnel_invalid_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Test getting personnel with invalid ID."""
    assert_404_response(
        client,
        "get",
        "/api/v1/personnel/invalid-personnel-id",
        admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )


async def test_update_personnel_invalid_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Test updating personnel with invalid ID."""
    update_data = {
        "rank": "Updated Rank",
    }

    response = client.patch(
        "/api/v1/personnel/invalid-personnel-id",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_personnel_from_different_estab_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_estab,
):
    """Test that personnel from different estab are not returned."""
    # Create personnel for different estab
    from parade_state.models.personnel import Personnel

    other_personnel = Personnel(
        estab_id=str(sample_estab.id) + "different",  # Different estab
        pers_no="12345",
        rank="Private",
        full_name="Other Person",
        unit="Other Unit",
        created_by="admin-user-id",
    )

    db_session.add(other_personnel)
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Other personnel should not be in the list
    other_found = False
    for personnel in data:
        if personnel["id"] == str(other_personnel.id):
            other_found = True
            break

    assert other_found is False


@pytest.mark.asyncio
async def test_get_personnel_from_different_estab_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_estab,
):
    """Test that getting personnel from different estab returns error."""
    # Create personnel for different estab
    from parade_state.models.personnel import Personnel

    other_personnel = Personnel(
        estab_id=str(sample_estab.id) + "different",  # Different estab
        pers_no="12345",
        rank="Private",
        full_name="Other Person",
        unit="Other Unit",
        created_by="admin-user-id",
    )

    db_session.add(other_personnel)
    await db_session.commit()

    response = client.get(
        f"/api/v1/personnel/{other_personnel.id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 400
    assert "does not belong to this deployment's establishment" in response.json()["detail"]


# ============================================================================
# Session 3 Tests: Audit Trail, Sorting, and Enhanced Validation
# ============================================================================


@pytest.mark.asyncio
async def test_update_personnel_sets_audit_trail(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that updating personnel sets audit trail fields."""
    import time

    # Get original personnel
    get_response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert get_response.status_code == 200
    original_data = get_response.json()
    original_updated_at = original_data.get("updated_at")

    # Wait a bit to ensure timestamp difference
    time.sleep(0.1)

    # Update personnel
    update_data = {
        "rank": "Updated Rank",
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["rank"] == "Updated Rank"
    assert data["updated_at"] is not None
    assert data["updated_by"] == str(sample_users["admin"].id)

    # Verify updated_at changed
    if original_updated_at:
        assert data["updated_at"] != original_updated_at


@pytest.mark.asyncio
async def test_list_personnel_sort_by_name_asc(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test sorting personnel by name ascending."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "name",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify names are in ascending order
    names = [p["name"] for p in data]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_list_personnel_sort_by_name_desc(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test sorting personnel by name descending."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "name",
            "sort_order": "desc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify names are in descending order
    names = [p["name"] for p in data]
    assert names == sorted(names, reverse=True)


@pytest.mark.asyncio
async def test_list_personnel_sort_by_rank(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test sorting personnel by rank."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "rank",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify ranks are sorted
    ranks = [p["rank"] for p in data]
    assert ranks == sorted(ranks)


@pytest.mark.asyncio
async def test_list_personnel_sort_by_status(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test sorting personnel by status."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "status",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify statuses are sorted
    statuses = [p["status"] for p in data]
    assert statuses == sorted(statuses)


@pytest.mark.asyncio
async def test_list_personnel_invalid_sort_field_ignored(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that invalid sort field is ignored (doesn't cause error)."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "invalid_field",  # Invalid field
            "sort_order": "asc",
        },
    )

    # Should not error, just ignore invalid sort field
    assert response.status_code == 200
    assert len(response.json()) > 0


@pytest.mark.asyncio
async def test_update_personnel_invalid_status(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that updating personnel with invalid status fails validation."""
    update_data = {
        "status": "invalid_status",  # Not 'active' or 'archived'
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    # Should fail validation
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_personnel_empty_rank(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that updating personnel with empty rank fails validation."""
    update_data = {
        "rank": "",  # Empty string
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    # Should fail validation
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_personnel_too_long_name(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that updating personnel with too long name fails validation."""
    update_data = {
        "name": "A" * 256,  # Exceeds max_length of 255
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    # Should fail validation
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_personnel_response_includes_audit_fields(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that personnel responses include audit trail fields."""
    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify audit fields are present
    assert "created_at" in data
    assert "updated_at" in data
    assert "created_by" in data
    assert "updated_by" in data


@pytest.mark.asyncio
async def test_list_personnel_with_filters_and_sorting(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test combining filters with sorting."""
    # Archive some personnel
    sample_personnel[0].status = "archived"
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "status": "archived",
            "sort_by": "name",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Should only return archived personnel
    assert all(p["status"] == "archived" for p in data)

    # Should be sorted by name
    names = [p["name"] for p in data]
    assert names == sorted(names)
