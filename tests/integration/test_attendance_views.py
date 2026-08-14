"""Tests for the attendance HTML views (issue #4 PR 3).

The view layer is thin over the already-tested attendance API; these tests
cover route wiring (unauthenticated redirects) and the one piece of new
view-layer logic — the user-facing roster is filtered to the caller's
assigned subunits.

Note: the auth helpers (get_current_user_optional / get_current_admin_user_optional)
are called directly inside the handlers rather than via Depends(), so we
monkeypatch the module-level references to inject a user.
"""

import pytest
from fastapi.testclient import TestClient


def test_admin_attendance_redirects_when_unauthenticated(client: TestClient):
    """Unauthenticated /admin/attendance redirects to login (302)."""
    response = client.get("/admin/attendance", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["location"]


def test_user_attendance_redirects_when_unauthenticated(client: TestClient):
    """Unauthenticated /attendance redirects to login (302)."""
    response = client.get("/attendance", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_user_attendance_filters_roster_to_assigned_subunits(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
    monkeypatch,
):
    """A regular user's /attendance roster only shows their assigned subunits.

    No assignments → empty roster (deny-by-default at the view layer).
    Granting Platoon 1 shows only Platoon 1 personnel.
    """
    from parade_state.web import attendance as web_attendance
    from parade_state.models import UserSubunitAssignment
    from parade_state.db import get_session_maker

    regular = sample_users["user"]

    async def _fake_current_user(_request):
        return regular

    monkeypatch.setattr(web_attendance, "get_current_user_optional", _fake_current_user)

    # No assignments → empty roster.
    response = client.get(
        "/attendance", params={"nominal_roll_id": str(sample_nominal_roll.id)}
    )
    assert response.status_code == 200
    assert "John Doe" not in response.text  # personnel[0], Platoon 1

    # Grant Platoon 1 → Platoon 1 personnel appear, Platoon 2 do not.
    sm = get_session_maker()
    async with sm() as db:
        db.add(
            UserSubunitAssignment(
                user_id=str(regular.id),
                nominal_roll_id=str(sample_nominal_roll.id),
                sub_unit_1="Platoon 1",
                created_by=str(sample_users["admin"].id),
            )
        )
        await db.commit()

    response = client.get(
        "/attendance", params={"nominal_roll_id": str(sample_nominal_roll.id)}
    )
    assert response.status_code == 200
    assert "John Doe" in response.text  # Platoon 1
    assert "Bob Johnson" not in response.text  # Platoon 2


@pytest.mark.asyncio
async def test_admin_attendance_rejects_non_super_admin(
    client: TestClient,
    sample_users,
    monkeypatch,
):
    """An authenticated non-super-admin is redirected away from /admin/attendance."""
    from parade_state import admin_routes

    admin = sample_users["admin"]  # role='admin', not super_admin

    async def _fake_current_admin(_request):
        return admin

    monkeypatch.setattr(admin_routes, "get_current_admin_user_optional", _fake_current_admin)

    response = client.get("/admin/attendance", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/admin"
