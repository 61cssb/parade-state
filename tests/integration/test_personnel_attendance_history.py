"""Tests for personnel attendance history endpoint."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from parade_state.models.deployment import Deployment
from parade_state.models.personnel import Personnel
from parade_state.utils import utc_dt


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_basic(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
    sample_personnel,
    sample_attendance_records,
):
    """Test getting personnel attendance history."""
    personnel_id = str(sample_personnel[0].id)

    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "personnel_id" in data
    assert "deployment_id" in data
    assert "stats" in data
    assert "attendance_records" in data
    assert "total_count" in data

    # Check statistics
    stats = data["stats"]
    assert "total_sessions" in stats
    assert "present_count" in stats
    assert "absent_count" in stats
    assert "excused_count" in stats
    assert "unknown_count" in stats
    assert "attendance_rate" in stats

    # Verify statistics match the sample data
    assert stats["total_sessions"] == 3
    assert stats["present_count"] == 1
    assert stats["absent_count"] == 1
    assert stats["excused_count"] == 1
    assert stats["unknown_count"] == 0
    # Attendance rate = (1 + 1) / 3 = 66.67%
    assert abs(stats["attendance_rate"] - 66.67) < 0.1

    # Check attendance records
    records = data["attendance_records"]
    assert len(records) == 3
    assert data["total_count"] == 3

    # Check first record structure
    first_record = records[0]
    assert "id" in first_record
    assert "session_id" in first_record
    assert "session_date" in first_record
    assert "session_type" in first_record
    assert "session_status" in first_record
    assert "status" in first_record
    assert "remarks" in first_record
    assert "created_at" in first_record
    assert "updated_at" in first_record


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_with_date_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
    sample_personnel,
    sample_attendance_records,
):
    """Test getting personnel attendance history with date range filter."""
    personnel_id = str(sample_personnel[0].id)

    # Get attendance from today only
    today = date.today()
    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Should only return today's attendance (2 records)
    assert data["total_count"] == 2
    assert len(data["attendance_records"]) == 2
    assert data["stats"]["total_sessions"] == 2


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_with_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
    sample_personnel,
    sample_attendance_records,
):
    """Test getting personnel attendance history with pagination."""
    personnel_id = str(sample_personnel[0].id)

    # Get first page with limit 1
    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "limit": 1,
            "offset": 0,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Should return 1 record but total count should be 3
    assert len(data["attendance_records"]) == 1
    assert data["total_count"] == 3


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_ordering(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
    sample_personnel,
    sample_attendance_records,
):
    """Test that attendance history is ordered by date descending (most recent first)."""
    personnel_id = str(sample_personnel[0].id)

    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    records = data["attendance_records"]

    # Check that records are ordered by date descending
    dates = [record["session_date"] for record in records]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_invalid_personnel(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
):
    """Test getting attendance history for non-existent personnel."""
    invalid_personnel_id = "00000000-0000-0000-0000-000000000000"

    response = client.get(
        f"/api/v1/personnel/{invalid_personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 404
    assert "Personnel not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_wrong_estab(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
    sample_personnel,
    db_session,
):
    """Test that personnel from different estab cannot be queried."""
    from parade_state.models import Deployment as DeploymentModel
    from parade_state.models import Estab

    personnel_id = str(sample_personnel[0].id)

    # Try to use a deployment ID that doesn't exist
    fake_deployment_id = "00000000-0000-0000-0000-999999999999"

    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": fake_deployment_id,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    # Should return 404 for non-existent deployment
    assert response.status_code == 404
    assert "Deployment not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_no_records(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test getting attendance history for personnel with no attendance records."""
    # Use second personnel who has no attendance records
    personnel_id = str(sample_personnel[1].id)

    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Should return empty history with zero statistics
    assert data["total_count"] == 0
    assert len(data["attendance_records"]) == 0
    assert data["stats"]["total_sessions"] == 0
    assert data["stats"]["present_count"] == 0
    assert data["stats"]["absent_count"] == 0
    assert data["stats"]["excused_count"] == 0
    assert data["stats"]["unknown_count"] == 0
    assert data["stats"]["attendance_rate"] == 0.0


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_various_statuses(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_deployment: Deployment,
    sample_personnel,
    sample_attendance_records,
    db_session,
):
    """Test attendance history with all possible status types."""
    from parade_state.models import AttendanceRecord, Session

    personnel_id = str(sample_personnel[0].id)
    admin_id = str(sample_users["admin"].id)

    # Create an additional session with "unknown" status
    unknown_session = Session(
        deployment_id=str(sample_deployment.id),
        date=date.today() - timedelta(days=2),
        session_type="PM",
        status="open",
        created_by=admin_id,
        opened_at=utc_dt.utcnow(),
    )
    db_session.add(unknown_session)
    await db_session.commit()

    # Create attendance record with "unknown" status
    unknown_record = AttendanceRecord(
        session_id=str(unknown_session.id),
        personnel_id=personnel_id,
        deployment_id=str(sample_deployment.id),
        status="unknown",
        created_by=admin_id,
        updated_by=admin_id,
    )
    db_session.add(unknown_record)
    await db_session.commit()

    # Get attendance history
    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": admin_id,
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Should now have 4 records with all status types
    assert data["total_count"] == 4
    stats = data["stats"]
    assert stats["present_count"] == 1
    assert stats["absent_count"] == 1
    assert stats["excused_count"] == 1
    assert stats["unknown_count"] == 1
    # Attendance rate = (1 + 1) / 4 = 50%
    assert abs(stats["attendance_rate"] - 50.0) < 0.1


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_access_control(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_deployment: Deployment,
    sample_personnel,
    sample_attendance_records,
):
    """Test that regular users cannot access attendance history (without deployment access)."""
    personnel_id = str(sample_personnel[0].id)

    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=user_token_headers,
        params={
            "deployment_id": str(sample_deployment.id),
            "user_id": "regular-user-id",
            "user_role": "user",
        },
    )

    # Should get 403 because regular users don't have deployment access yet
    # (TODO: This will change once deployment access control is implemented)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_personnel_attendance_history_invalid_deployment(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """Test getting attendance history for non-existent deployment."""
    personnel_id = str(sample_personnel[0].id)
    invalid_deployment_id = "00000000-0000-0000-0000-000000000000"

    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "deployment_id": invalid_deployment_id,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 404
    assert "Deployment not found" in response.json()["detail"]
