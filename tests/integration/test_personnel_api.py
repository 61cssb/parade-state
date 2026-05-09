"""Tests for personnel management API endpoints."""

import pytest
from datetime import date, datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.deployment import Deployment, DeploymentNotes, DeploymentPersonnelOverride
from parade_state.models.personnel import Personnel


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
    response = client.get(
        "/api/v1/personnel",
        headers=user_token_headers,
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
    )

    assert response.status_code == 403
    assert "Only admins can list personnel without deployment context" in response.json()["detail"]


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


@pytest.mark.asyncio
async def test_get_personnel_by_id_without_deployment_context_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_personnel,
):
    """Test that regular users cannot get personnel without deployment context."""
    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=user_token_headers,
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
    )

    assert response.status_code == 403
    assert "Only admins can view personnel without deployment context" in response.json()["detail"]


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


@pytest.mark.asyncio
async def test_update_personnel_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_personnel,
):
    """Test that regular users cannot update personnel."""
    update_data = {
        "rank": "Updated Rank",
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=user_token_headers,
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
        json=update_data,
    )

    assert response.status_code == 403
    assert "Only admins can update personnel records" in response.json()["detail"]


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


@pytest.mark.asyncio
async def test_list_personnel_with_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test listing personnel with pagination."""
    # Request first page
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "limit": 2,
            "offset": 0,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) <= 2

    # Request second page
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "limit": 2,
            "offset": 2,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) <= 2


@pytest.mark.asyncio
async def test_list_personnel_invalid_deployment_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Test listing personnel with invalid deployment ID."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "deployment_id": "invalid-deployment-id",
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_personnel_invalid_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Test getting personnel with invalid ID."""
    response = client.get(
        "/api/v1/personnel/invalid-personnel-id",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 404


@pytest.mark.asyncio
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
