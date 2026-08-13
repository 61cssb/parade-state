"""Tests for the personnel attendance history endpoint (reworked model).

Attendance is NR/Tagging-scoped with AM/PM slots; history returns per-day
rows and counts each AM/PM slot independently toward totals.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_history_basic_stats(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
    sample_attendance,
):
    """History returns the reshaped fields and AM/PM-bucketed stats."""
    personnel_id = str(sample_personnel[0].id)

    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert data["personnel_id"] == personnel_id
    assert "nominal_roll_id" in data
    assert "stats" in data
    assert "attendance_records" in data

    # Fixture: person 0 has 2 days × 2 slots = 4 slots.
    # present-like: AM today (present), AM yesterday (late) = 2.
    # absent: PM today, PM yesterday = 2.
    stats = data["stats"]
    assert stats["total_slots"] == 4
    assert stats["present_count"] == 2
    assert stats["absent_count"] == 2
    assert abs(stats["attendance_rate"] - 50.0) < 0.1

    # Record shape (no session fields; AM/PM columns instead).
    record = data["attendance_records"][0]
    for key in (
        "id",
        "nominal_roll_id",
        "tagging_id",
        "date",
        "status_am",
        "remarks_am",
        "status_pm",
        "remarks_pm",
    ):
        assert key in record


@pytest.mark.asyncio
async def test_history_date_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
    sample_attendance,
):
    """Date range filter narrows to a single day."""
    personnel_id = str(sample_personnel[0].id)
    today = date.today().isoformat()

    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "date_from": today,
            "date_to": today,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert data["stats"]["total_slots"] == 2  # AM + PM


@pytest.mark.asyncio
async def test_history_ordering_desc(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
    sample_attendance,
):
    """Records are ordered by date descending."""
    personnel_id = str(sample_personnel[0].id)
    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert response.status_code == 200
    records = response.json()["attendance_records"]
    dates = [r["date"] for r in records]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.asyncio
async def test_history_invalid_personnel_404(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Unknown personnel returns 404."""
    response = client.get(
        "/api/v1/personnel/00000000-0000-0000-0000-000000000000/attendance-history",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_history_wrong_nominal_roll_400(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """Passing a mismatched nominal_roll_id returns 400."""
    personnel_id = str(sample_personnel[0].id)
    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "nominal_roll_id": "00000000-0000-0000-0000-000000000000",
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_history_no_records(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """Personnel with no attendance rows get zeroed stats."""
    # Personnel 2 has no attendance in the fixture.
    personnel_id = str(sample_personnel[2].id)
    response = client.get(
        f"/api/v1/personnel/{personnel_id}/attendance-history",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 0
    assert data["stats"]["total_slots"] == 0
    assert data["stats"]["attendance_rate"] == 0.0
