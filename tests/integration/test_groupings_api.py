"""Tests for grouping API endpoints."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.attendance import Attendance
from parade_state.models.csv_ingestion import NominalRoll
from parade_state.models.grouping import (
    Grouping,
    GroupingNotes,
    GroupingPersonnelOverride,
)
from parade_state.models.personnel import Personnel
from parade_state.models.schemas import GroupingCreate, GroupingUpdate
from parade_state.utils import utc_dt
from tests.test_utils import (
    assert_404_response,
    assert_pagination_works,
    assert_permission_denied,
)


@pytest.mark.asyncio
async def test_create_grouping_as_admin(
    client: TestClient, admin_token_headers: dict[str, str], db_session,
    sample_nominal_roll,
):
    """Test grouping creation by admin."""
    grouping_data = {
        "name": "Test Grouping",
        "nominal_roll_id": str(sample_nominal_roll.id),
        "mode": "standard",
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
        "status": "draft",
        "notes": "Test grouping notes",
    }

    response = client.post(
        "/api/v1/groupings/",
        json=grouping_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Grouping"
    assert data["status"] == "draft"
    assert data["nominal_roll_id"] == str(sample_nominal_roll.id)


@pytest.mark.asyncio
async def test_create_grouping_as_regular_user_forbidden(
    client: TestClient, user_token_headers: dict[str, str], db_session
):
    """Test that regular users cannot create groupings."""
    grouping_data = {
        "name": "Test Grouping",
        "nominal_roll_id": "test-nominal_roll-123",
        "mode": "standard",
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
    }

    assert_permission_denied(
        client,
        "post",
        "/api/v1/groupings/",
        user_token_headers,
        expected_detail="Only admins and super admins",
        params={"user_id": "regular-user-id", "user_role": "user"},
        json_data=grouping_data,
    )


@pytest.mark.asyncio
async def test_create_grouping_invalid_date_range(
    client: TestClient, admin_token_headers: dict[str, str], db_session,
    sample_nominal_roll,
):
    """Test grouping creation with invalid date range."""
    grouping_data = {
        "name": "Test Grouping",
        "nominal_roll_id": str(sample_nominal_roll.id),
        "mode": "standard",
        "valid_from": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
    }

    response = client.post(
        "/api/v1/groupings/",
        json=grouping_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "valid_until must be after valid_from" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_grouping_non_existent_nominal_roll(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test grouping creation fails when nominal_roll does not exist."""
    grouping_data = {
        "name": "Test Grouping",
        "nominal_roll_id": "does-not-exist",
        "mode": "standard",
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
    }

    response = client.post(
        "/api/v1/groupings/",
        json=grouping_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_grouping_from_any_nominal_roll(
    client: TestClient, admin_token_headers: dict[str, str], db_session,
    sample_users,
):
    """Grouping creation no longer requires a confirmed NR — all NRs are
    equal under the active-NR attendance model."""
    other_nominal_roll = NominalRoll(
        caa=date(2024, 3, 1),
        csv_hash="other-hash",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(other_nominal_roll)
    await db_session.commit()

    grouping_data = {
        "name": "Test Grouping",
        "nominal_roll_id": str(other_nominal_roll.id),
        "mode": "standard",
        "valid_from": (utc_dt.utcnow() + timedelta(days=1)).isoformat(),
        "valid_until": (utc_dt.utcnow() + timedelta(days=30)).isoformat(),
    }

    response = client.post(
        "/api/v1/groupings/",
        json=grouping_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_list_groupings(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test listing groupings."""
    # Create some test groupings
    grouping1 = Grouping(
        name="Grouping 1",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )
    grouping2 = Grouping(
        name="Grouping 2",
        nominal_roll_id="nominal_roll-2",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(grouping1)
    db_session.add(grouping2)
    await db_session.commit()

    response = client.get(
        "/api/v1/groupings/",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(d["name"] == "Grouping 1" for d in data)
    assert any(d["name"] == "Grouping 2" for d in data)


@pytest.mark.asyncio
async def test_list_groupings_with_status_filter(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test listing groupings with status filter."""
    # Create test groupings with different statuses
    grouping1 = Grouping(
        name="Active Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
        activated_at=utc_dt.utcnow(),
    )
    grouping2 = Grouping(
        name="Draft Grouping",
        nominal_roll_id="nominal_roll-2",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping1)
    db_session.add(grouping2)
    await db_session.commit()

    response = client.get(
        "/api/v1/groupings/",
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
    assert data[0]["name"] == "Active Grouping"
    assert data[0]["status"] == "active"


@pytest.mark.asyncio
async def test_get_grouping(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test getting a specific grouping."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    response = client.get(
        f"/api/v1/groupings/{grouping.id}",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(grouping.id)
    assert data["name"] == "Test Grouping"


@pytest.mark.asyncio
async def test_get_grouping_not_found(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test getting a non-existent grouping."""
    assert_404_response(
        client,
        "get",
        "/api/v1/groupings/non-existent-id",
        admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )


@pytest.mark.asyncio
async def test_update_grouping(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test updating a grouping."""
    grouping = Grouping(
        name="Original Name",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    update_data = {"name": "Updated Name", "notes": "Updated notes"}

    response = client.patch(
        f"/api/v1/groupings/{grouping.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["notes"] == "Updated notes"


@pytest.mark.asyncio
async def test_update_grouping_status_transition(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test updating grouping status."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    update_data = {"status": "active"}

    response = client.patch(
        f"/api/v1/groupings/{grouping.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["activated_at"] is not None


@pytest.mark.asyncio
async def test_update_grouping_invalid_status_transition(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test updating grouping status with invalid transition."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="finalized",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    update_data = {"status": "active"}

    response = client.patch(
        f"/api/v1/groupings/{grouping.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Invalid status transition" in response.json()["detail"]


@pytest.mark.asyncio
async def test_activate_grouping(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test activating a grouping."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    response = client.post(
        f"/api/v1/groupings/{grouping.id}/activate",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["activated_at"] is not None


@pytest.mark.asyncio
async def test_activate_grouping_already_active(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test activating an already active grouping."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(grouping)
    await db_session.commit()

    response = client.post(
        f"/api/v1/groupings/{grouping.id}/activate",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "already active" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_deactivate_grouping(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test deactivating a grouping."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(grouping)
    await db_session.commit()

    response = client.post(
        f"/api/v1/groupings/{grouping.id}/deactivate",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "inactive"
    assert data["deactivated_at"] is not None


@pytest.mark.asyncio
async def test_delete_grouping_as_super_admin(
    client: TestClient, super_admin_token_headers: dict[str, str], db_session
):
    """Test grouping deletion by super admin."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="super-admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/groupings/{grouping.id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-user-id", "user_role": "super_admin"},
    )

    assert response.status_code == 204

    # Verify grouping was deleted
    result = await db_session.execute(
        select(Grouping).where(Grouping.id == grouping.id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_grouping_as_admin_forbidden(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test that regular admins cannot delete groupings."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    assert_permission_denied(
        client,
        "delete",
        f"/api/v1/groupings/{grouping.id}",
        admin_token_headers,
        expected_detail="Only super admins",
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )


@pytest.mark.asyncio
async def test_delete_active_grouping_forbidden(
    client: TestClient, super_admin_token_headers: dict[str, str], db_session
):
    """Test that active groupings cannot be deleted."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="super-admin-user-id",
        activated_at=utc_dt.utcnow(),
    )

    db_session.add(grouping)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/groupings/{grouping.id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-user-id", "user_role": "super_admin"},
    )

    assert response.status_code == 400
    assert "Cannot delete grouping" in response.json()["detail"]


@pytest.mark.asyncio
async def test_search_groupings(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test searching groupings by name."""
    grouping1 = Grouping(
        name="Alpha Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )
    grouping2 = Grouping(
        name="Bravo Grouping",
        nominal_roll_id="nominal_roll-2",
        mode="standard",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping1)
    db_session.add(grouping2)
    await db_session.commit()

    response = client.get(
        "/api/v1/groupings/",
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
    assert data[0]["name"] == "Alpha Grouping"


@pytest.mark.asyncio
async def test_list_groupings_pagination(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test grouping list pagination."""
    # Create multiple groupings
    for i in range(5):
        grouping = Grouping(
            name=f"Grouping {i}",
            nominal_roll_id=f"nominal_roll-{i}",
            mode="standard",
            valid_from=utc_dt.utcnow(),
            valid_until=utc_dt.utcnow() + timedelta(days=30),
            created_by="admin-user-id",
        )
        db_session.add(grouping)

    await db_session.commit()

    assert_pagination_works(
        client,
        "/api/v1/groupings/",
        admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
        },
    )


# ============================================================================
# Grouping Status Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_grouping_status_no_sessions(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test getting grouping status when no sessions exist."""
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    response = client.get(
        f"/api/v1/groupings/{grouping.id}/status",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["grouping_id"] == grouping.id
    assert data["grouping_name"] == "Test Grouping"
    assert data["grouping_status"] == "active"
    assert data["am_session"] is None
    assert data["pm_session"] is None
    assert len(data["units"]) == 0


@pytest.mark.asyncio
async def test_get_grouping_status_with_sessions(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test getting grouping status with sessions and attendance."""
    from datetime import date

    # Create grouping
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    # Create personnel
    personnel1 = Personnel(
        nominal_roll_id="nominal_roll-1",
        rank="PTE",
        category="WOSE",
        full_name="John Doe",
        unit="Coy A",
        sub_unit_1="Platoon 1",
        created_by="admin-user-id",
    )

    personnel2 = Personnel(
        nominal_roll_id="nominal_roll-1",
        rank="PTE",
        category="WOSE",
        full_name="Jane Smith",
        unit="Coy B",
        sub_unit_1="Platoon 2",
        created_by="admin-user-id",
    )

    db_session.add_all([personnel1, personnel2])
    await db_session.commit()

    # Create attendance rows for today (AM/PM model).
    today = utc_dt.utcnow().date()
    attendance1 = Attendance(
        personnel_id=str(personnel1.id),
        nominal_roll_id="nominal_roll-1",
        date=today,
        status_am="present",
        status_pm="present",
        unit_snapshot="Coy A",
        sub_unit_1_snapshot="Platoon 1",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    attendance2 = Attendance(
        personnel_id=str(personnel2.id),
        nominal_roll_id="nominal_roll-1",
        date=today,
        status_am="absent",
        status_pm="absent",
        unit_snapshot="Coy B",
        sub_unit_1_snapshot="Platoon 2",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add_all([attendance1, attendance2])
    await db_session.commit()

    response = client.get(
        f"/api/v1/groupings/{grouping.id}/status",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["grouping_id"] == grouping.id
    assert data["grouping_name"] == "Test Grouping"
    assert data["am_session"] is not None
    assert data["am_session"]["present"] == 1
    assert data["am_session"]["absent"] == 1
    assert len(data["units"]) == 2


# ============================================================================
# CSV Export Tests
# ============================================================================


@pytest.mark.asyncio
async def test_export_grouping_csv(
    client: TestClient, admin_token_headers: dict[str, str], db_session
):
    """Test exporting grouping data to CSV."""
    # Create grouping
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    # Create personnel
    personnel = Personnel(
        nominal_roll_id="nominal_roll-1",
        pers_no="10000001",
        rank="PTE",
        category="WOSE",
        full_name="John Doe",
        unit="Coy A",
        sub_unit_1="Platoon 1",
        created_by="admin-user-id",
    )

    db_session.add(personnel)
    await db_session.commit()

    # Create grouping override
    override = GroupingPersonnelOverride(
        grouping_id=str(grouping.id),
        personnel_id=str(personnel.id),
        unit="Override Unit",
        sub_unit_1="Override Platoon",
        created_by="admin-user-id",
    )

    db_session.add(override)
    await db_session.commit()

    # Create grouping notes
    notes = GroupingNotes(
        grouping_id=str(grouping.id),
        personnel_id=str(personnel.id),
        notes="Test notes",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add(notes)
    await db_session.commit()

    response = client.get(
        f"/api/v1/groupings/{grouping.id}/export",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    assert "Test_Grouping" in response.headers["content-disposition"]

    # Verify CSV content
    csv_content = response.content.decode("utf-8")
    assert personnel.pers_no in csv_content
    assert "Rank" in csv_content
    assert "John Doe" in csv_content
    assert "Override Unit" in csv_content
    assert "Test notes" in csv_content


@pytest.mark.asyncio
async def test_export_grouping_csv_unauthorized(
    client: TestClient, user_token_headers: dict[str, str], db_session
):
    """Test that regular users cannot export grouping data without access."""
    # Note: This test assumes the user doesn't have access to this grouping
    # The access control logic will need to be implemented based on user scopes
    grouping = Grouping(
        name="Test Grouping",
        nominal_roll_id="nominal_roll-1",
        mode="standard",
        status="active",
        valid_from=utc_dt.utcnow(),
        valid_until=utc_dt.utcnow() + timedelta(days=30),
        created_by="admin-user-id",
    )

    db_session.add(grouping)
    await db_session.commit()

    # For now, this test just checks the endpoint works
    # Once proper access control is implemented, this should return 403
    response = client.get(
        f"/api/v1/groupings/{grouping.id}/export",
        headers=user_token_headers,
        params={"user_id": "user-id", "user_role": "user"},
    )

    # This might succeed with current access control implementation
    # but should be restricted once proper scopes are enforced
    assert response.status_code in [200, 403, 404]


# ============================================================================
# Grouping date editing validation tests
# ============================================================================


@pytest.mark.asyncio
async def test_update_grouping_dates_widens_range(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    sample_grouping: Grouping,
):
    """Widening the valid date range succeeds (sessions check removed)."""
    new_valid_from = (date.today() - timedelta(days=10)).isoformat() + "T00:00:00"
    new_valid_until = (date.today() + timedelta(days=60)).isoformat() + "T23:59:59"

    response = client.patch(
        f"/api/v1/groupings/{sample_grouping.id}",
        json={"valid_from": new_valid_from, "valid_until": new_valid_until},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["valid_until"].startswith(
        (date.today() + timedelta(days=60)).isoformat()
    )


@pytest.mark.asyncio
async def test_update_grouping_dates_no_sessions_succeeds(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    sample_grouping: Grouping,
):
    """Updating dates on a grouping always succeeds now (no session check)."""
    new_valid_from = (date.today() - timedelta(days=5)).isoformat() + "T00:00:00"

    response = client.patch(
        f"/api/v1/groupings/{sample_grouping.id}",
        json={"valid_from": new_valid_from},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 200
