"""Tests for attendance management API endpoints."""

import pytest
from datetime import date, datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.attendance import AttendanceRecord, Session
from parade_state.models.deployment import Deployment, DeploymentNotes, DeploymentPersonnelOverride
from parade_state.models.personnel import Personnel
from parade_state.utils import utc_dt
from tests.test_utils import assert_pagination_works, assert_404_response, assert_permission_denied


@pytest.mark.asyncio
async def test_create_attendance_record_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test attendance record creation by admin for open session."""
    session_date = date.today()

    # Create an open session
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

    attendance_data = {
        "session_id": str(session.id),
        "personnel_id": str(sample_personnel[0].id),
        "status": "present",
        "remarks": "On time",
    }

    response = client.post(
        "/api/v1/attendance/",
        json=attendance_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["session_id"] == str(session.id)
    assert data["personnel_id"] == str(sample_personnel[0].id)
    assert data["status"] == "present"
    assert data["remarks"] == "On time"
    assert "id" in data
    assert data["is_retroactive_edit"] is False  # Today's session


@pytest.mark.asyncio
async def test_create_attendance_record_for_closed_session_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that attendance cannot be created for closed sessions."""
    session_date = date.today()

    # Create a closed session
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

    attendance_data = {
        "session_id": str(session.id),
        "personnel_id": str(sample_personnel[0].id),
        "status": "present",
    }

    response = client.post(
        "/api/v1/attendance/",
        json=attendance_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Cannot modify attendance for closed sessions" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_attendance_record_with_deployment_notes_snapshot(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
    sample_users,
):
    """Test that attendance creation snapshots deployment notes."""
    session_date = date.today()

    # Create deployment notes for personnel
    notes = DeploymentNotes(
        deployment_id=str(sample_deployment.id),
        personnel_id=str(sample_personnel[0].id),
        notes="Medical exemption granted",
        created_by=str(sample_users["admin"].id),
        updated_by=str(sample_users["admin"].id),
    )

    db_session.add(notes)

    # Create an open session
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

    attendance_data = {
        "session_id": str(session.id),
        "personnel_id": str(sample_personnel[0].id),
        "status": "excused",
    }

    response = client.post(
        "/api/v1/attendance/",
        json=attendance_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["notes_snapshot"] == "Medical exemption granted"


@pytest.mark.asyncio
async def test_create_attendance_record_with_personnel_override_snapshot(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
    sample_users,
):
    """Test that attendance creation snapshots personnel overrides."""
    session_date = date.today()

    # Create personnel override
    override = DeploymentPersonnelOverride(
        deployment_id=str(sample_deployment.id),
        personnel_id=str(sample_personnel[0].id),
        unit="Temp Unit",
        sub_unit_1="Temp Platoon",
        sub_unit_2="Temp Section",
        created_by=str(sample_users["admin"].id),
    )

    db_session.add(override)

    # Create an open session
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

    attendance_data = {
        "session_id": str(session.id),
        "personnel_id": str(sample_personnel[0].id),
        "status": "present",
    }

    response = client.post(
        "/api/v1/attendance/",
        json=attendance_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["unit_snapshot"] == "Temp Unit"
    assert data["sub_unit_1_snapshot"] == "Temp Platoon"
    assert data["sub_unit_2_snapshot"] == "Temp Section"


@pytest.mark.asyncio
async def test_create_attendance_record_retroactive_detection(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
    sample_users,
):
    """Test retroactive edit detection for past sessions."""
    # Create a session for yesterday (in UTC to ensure it's actually in the past)
    yesterday = (utc_dt.utcnow() - timedelta(days=1)).date()
    admin_id = str(sample_users["admin"].id)

    session = Session(
        deployment_id=str(sample_deployment.id),
        date=yesterday,
        session_type="AM",
        status="open",
        created_by=admin_id,
        opened_at=utc_dt.utcnow(),
    )

    db_session.add(session)
    await db_session.commit()

    attendance_data = {
        "session_id": str(session.id),
        "personnel_id": str(sample_personnel[0].id),
        "status": "present",
    }

    response = client.post(
        "/api/v1/attendance/",
        json=attendance_data,
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["is_retroactive_edit"] is True  # Yesterday's session


@pytest.mark.asyncio
async def test_create_duplicate_attendance_record_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that duplicate attendance records are forbidden."""
    session_date = date.today()

    # Create session
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

    # Create first attendance record
    attendance_data = {
        "session_id": str(session.id),
        "personnel_id": str(sample_personnel[0].id),
        "status": "present",
    }

    response = client.post(
        "/api/v1/attendance/",
        json=attendance_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201

    # Try to create duplicate
    response = client.post(
        "/api/v1/attendance/",
        json=attendance_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_attendance_records(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test listing attendance records."""
    session_date = date.today()

    # Create session
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

    # Create attendance records
    attendance1 = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="present",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    attendance2 = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[1].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add_all([attendance1, attendance2])
    await db_session.commit()

    response = client.get(
        "/api/v1/attendance/",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(a["status"] == "present" for a in data)
    assert any(a["status"] == "absent" for a in data)


@pytest.mark.asyncio
async def test_list_attendance_records_with_session_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test listing attendance records filtered by session."""
    session_date = date.today()

    # Create sessions
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

    # Create attendance records for both sessions
    attendance1 = AttendanceRecord(
        session_id=str(session1.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="present",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    attendance2 = AttendanceRecord(
        session_id=str(session2.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add_all([attendance1, attendance2])
    await db_session.commit()

    # Filter by first session
    response = client.get(
        "/api/v1/attendance/",
        headers=admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
            "session_id": str(session1.id),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["session_id"] == str(session1.id)


@pytest.mark.asyncio
async def test_update_attendance_record(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test updating an attendance record."""
    session_date = date.today()

    # Create session
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

    # Create attendance record
    attendance = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add(attendance)
    await db_session.commit()

    update_data = {"status": "present", "remarks": "Arrived late"}

    response = client.patch(
        f"/api/v1/attendance/{attendance.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "present"
    assert data["remarks"] == "Arrived late"
    assert data["last_edit_at"] is not None
    assert data["last_edit_by"] == "admin-user-id"


@pytest.mark.asyncio
async def test_update_attendance_record_for_closed_session_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that attendance cannot be updated for closed sessions."""
    session_date = date.today()

    # Create closed session
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

    # Create attendance record
    attendance = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add(attendance)
    await db_session.commit()

    update_data = {"status": "present"}

    response = client.patch(
        f"/api/v1/attendance/{attendance.id}",
        json=update_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Cannot modify attendance for closed sessions" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_attendance_record_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test attendance record deletion by admin."""
    session_date = date.today()

    # Create session
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

    # Create attendance record
    attendance = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add(attendance)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/attendance/{attendance.id}",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 204

    # Verify record was deleted
    result = await db_session.execute(
        select(AttendanceRecord).where(AttendanceRecord.id == attendance.id)
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_attendance_record_as_regular_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that regular users cannot delete attendance records."""
    session_date = date.today()

    # Create session
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

    # Create attendance record
    attendance = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add(attendance)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/attendance/{attendance.id}",
        headers=user_token_headers,
        params={"user_id": "regular-user-id", "user_role": "user"},
    )

    assert response.status_code == 403
    assert "Only admins and super admins" in response.json()["detail"]


@pytest.mark.asyncio
async def test_bulk_create_attendance_records(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test bulk creation of attendance records."""
    session_date = date.today()

    # Create session
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

    bulk_data = {
        "attendance_records": [
            {
                "session_id": str(session.id),
                "personnel_id": str(sample_personnel[0].id),
                "status": "present",
                "remarks": "On time",
            },
            {
                "session_id": str(session.id),
                "personnel_id": str(sample_personnel[1].id),
                "status": "absent",
            },
            {
                "session_id": str(session.id),
                "personnel_id": str(sample_personnel[2].id),
                "status": "excused",
                "remarks": "Medical leave",
            },
        ]
    }

    response = client.post(
        "/api/v1/attendance/bulk/create",
        json=bulk_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 201
    data = response.json()
    assert len(data) == 3
    assert all("id" in record for record in data)


@pytest.mark.asyncio
async def test_bulk_create_attendance_records_atomic_rollback(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that bulk creation rolls back on error."""
    session_date = date.today()

    # Create session
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

    # Create one existing record to cause conflict
    existing_attendance = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="present",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add(existing_attendance)
    await db_session.commit()

    bulk_data = {
        "attendance_records": [
            {
                "session_id": str(session.id),
                "personnel_id": str(sample_personnel[0].id),  # Duplicate
                "status": "absent",
            },
            {
                "session_id": str(session.id),
                "personnel_id": str(sample_personnel[1].id),
                "status": "present",
            },
        ]
    }

    response = client.post(
        "/api/v1/attendance/bulk/create",
        json=bulk_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    # Should succeed but skip duplicate
    assert response.status_code == 201
    data = response.json()
    # Should only create the non-duplicate record
    assert len(data) == 1


@pytest.mark.asyncio
async def test_bulk_update_attendance_records(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test bulk updating of attendance records."""
    session_date = date.today()

    # Create session
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

    # Create attendance records
    attendance1 = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    attendance2 = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[1].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add_all([attendance1, attendance2])
    await db_session.commit()

    bulk_data = {
        "attendance_records": [
            {
                "id": str(attendance1.id),
                "status": "present",
                "remarks": "Arrived late",
            },
            {
                "id": str(attendance2.id),
                "status": "excused",
                "remarks": "Medical leave",
            },
        ]
    }

    response = client.post(
        "/api/v1/attendance/bulk/update",
        json=bulk_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    if response.status_code != 200:
        print(f"Error response: {response.text}")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(a["status"] == "present" for a in data)
    assert any(a["status"] == "excused" for a in data)


@pytest.mark.asyncio
async def test_bulk_update_attendance_records_for_closed_session_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test that bulk updates fail for closed sessions."""
    session_date = date.today()

    # Create closed session
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

    # Create attendance record
    attendance = AttendanceRecord(
        session_id=str(session.id),
        personnel_id=str(sample_personnel[0].id),
        deployment_id=str(sample_deployment.id),
        status="absent",
        created_by="admin-user-id",
        updated_by="admin-user-id",
    )

    db_session.add(attendance)
    await db_session.commit()

    bulk_data = {
        "attendance_records": [
            {
                "id": str(attendance.id),
                "status": "present",
            }
        ]
    }

    response = client.post(
        "/api/v1/attendance/bulk/update",
        json=bulk_data,
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Cannot modify attendance for closed session" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_attendance_record_not_found(
    client: TestClient,
    admin_token_headers: dict[str, str],
):
    """Test getting a non-existent attendance record."""
    assert_404_response(
        client,
        "get",
        "/api/v1/attendance/non-existent-id",
        admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )


@pytest.mark.asyncio
async def test_list_attendance_records_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_deployment: Deployment,
    sample_personnel,
):
    """Test attendance record list pagination."""
    session_date = date.today()

    # Create session
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

    # Create multiple attendance records
    attendance_records = []
    for i, personnel in enumerate(sample_personnel):
        attendance = AttendanceRecord(
            session_id=str(session.id),
            personnel_id=str(personnel.id),
            deployment_id=str(sample_deployment.id),
            status="present" if i % 2 == 0 else "absent",
            created_by="admin-user-id",
            updated_by="admin-user-id",
        )
        attendance_records.append(attendance)

    db_session.add_all(attendance_records)
    await db_session.commit()

    assert_pagination_works(
        client,
        "/api/v1/attendance/",
        admin_token_headers,
        params={
            "user_id": "admin-user-id",
            "user_role": "admin",
        },
    )