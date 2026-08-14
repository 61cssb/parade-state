"""Behavioral tests for the reworked attendance API.

Covers the NR/Tagging-scoped AM/PM attendance model: active-scope gating,
bulk upsert, list, copy-remarks (AM/PM timing), and the scope-activation
endpoint.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_list_requires_nominal_roll_and_date(
    client: TestClient, sample_nominal_roll, sample_attendance_scope
):
    """Missing query params yield 422 (FastAPI validation)."""
    response = client.get("/api/v1/attendance/")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_returns_rows_for_date(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """List returns rows for the requested NR + date."""
    today = date.today().isoformat()
    response = client.get(
        "/api/v1/attendance/",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "date": today,
        },
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2  # two personnel have rows today


@pytest.mark.asyncio
async def test_upsert_refuses_when_nr_not_active(
    client: TestClient, sample_nominal_roll, sample_personnel, admin_id
):
    """When the NR is not the one active for attendance, upsert returns 400."""
    today = date.today().isoformat()
    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "admin"},
        json={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "records": [
                {
                    "personnel_id": str(sample_personnel[0].id),
                    "date": today,
                    "status_am": "present",
                    "status_pm": "absent",
                }
            ],
        },
    )
    assert response.status_code == 400
    assert "not active" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upsert_creates_then_updates(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    admin_subunit_assignment,
    admin_id,
):
    """Upsert creates a row, then a second upsert updates it in place."""
    today = date.today().isoformat()
    pid = str(sample_personnel[0].id)
    nr_id = str(sample_nominal_roll.id)

    # Create.
    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "admin"},
        json={
            "nominal_roll_id": nr_id,
            "records": [
                {
                    "personnel_id": pid,
                    "date": today,
                    "status_am": "present",
                    "remarks_am": "in",
                    "status_pm": "absent",
                }
            ],
        },
    )
    assert response.status_code == 200
    created = response.json()[0]
    assert created["status_am"] == "present"
    assert created["remarks_am"] == "in"

    # Update same row.
    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "admin"},
        json={
            "nominal_roll_id": nr_id,
            "records": [
                {
                    "personnel_id": pid,
                    "date": today,
                    "status_am": "late",
                    "remarks_am": "duty",
                    "status_pm": "present",
                }
            ],
        },
    )
    assert response.status_code == 200
    updated = response.json()[0]
    assert updated["id"] == created["id"]  # same row, updated in place
    assert updated["status_am"] == "late"
    assert updated["status_pm"] == "present"


@pytest.mark.asyncio
async def test_upsert_rejects_personnel_not_on_nr(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    admin_id,
):
    """Upsert rejects a personnel_id that is not on the NR.

    Run as super_admin so the request reaches personnel validation rather than
    being short-circuited by the Subunit-1 access check.
    """
    today = date.today().isoformat()
    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "super_admin"},
        json={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "records": [
                {
                    "personnel_id": "not-a-real-personnel-id",
                    "date": today,
                    "status_am": "present",
                    "status_pm": "absent",
                }
            ],
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_activate_attendance_requires_super_admin(
    client: TestClient, sample_nominal_roll, admin_id
):
    """Non-super-admins cannot mark an NR active for attendance."""
    response = client.post(
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}/activate-attendance",
        params={"user_id": admin_id, "user_role": "admin"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_activate_attendance_then_upsert_succeeds(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    admin_subunit_assignment,
    admin_id,
):
    """Marking the NR "Use for Attendance" unblocks attendance upsert."""
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()

    # Activate (as super-admin).
    response = client.post(
        f"/api/v1/nominal-rolls/{nr_id}/activate-attendance",
        params={"user_id": admin_id, "user_role": "super_admin"},
    )
    assert response.status_code == 200
    assert response.json()["attendance_active"] is True

    # Now upsert works.
    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "admin"},
        json={
            "nominal_roll_id": nr_id,
            "records": [
                {
                    "personnel_id": str(sample_personnel[0].id),
                    "date": today,
                    "status_am": "present",
                    "status_pm": "present",
                }
            ],
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_copy_remarks_is_well_formed(
    client: TestClient,
    sample_nominal_roll,
    sample_attendance_scope,
    sample_attendance,
    admin_subunit_assignment,
    admin_id,
):
    """copy-remarks returns the documented shape with slot/updated/skipped."""
    today = date.today().isoformat()
    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "date": today,
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slot"] in ("am", "pm")
    assert body["updated"] + body["skipped"] >= 1


@pytest.mark.asyncio
async def test_copy_remarks_refuses_when_not_active(
    client: TestClient, sample_nominal_roll, admin_id
):
    """copy-remarks refuses (400) when the NR isn't active for attendance."""
    today = date.today().isoformat()
    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "date": today,
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    assert response.status_code == 400
