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


def test_admin_attendance_page_removed(client: TestClient):
    """The admin attendance page is removed — its capabilities live in
    /attendance (Copy Remarks for super-admins, effective sub-unit filter)."""
    response = client.get("/admin/attendance", follow_redirects=False)
    assert response.status_code == 404


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
    """A non-super-admin's /attendance roster only shows their assigned subunits.

    No assignments → empty roster (deny-by-default at the view layer).
    Granting Platoon 1 shows only Platoon 1 personnel. (The viewer role is
    deferred, so the filtered user here is a plain admin.)
    """
    from parade_state.web import attendance as web_attendance
    from parade_state.models import UserSubunitAssignment
    from parade_state.db import get_session_maker

    admin = sample_users["admin"]

    async def _fake_current_user(_request):
        return admin

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
                user_id=str(admin.id),
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
async def test_user_attendance_shows_nr_picker_without_grouping_access(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
    monkeypatch,
):
    """Attendance is NR-scoped, not grouping-scoped: a non-super-admin with
    no grouping access and no subunit assignments still gets the NR picker
    (and the no-assignments hint), never the old "No accessible groupings"
    dead end."""
    from parade_state.web import attendance as web_attendance

    async def _fake_current_user(_request):
        return sample_users["admin"]

    monkeypatch.setattr(web_attendance, "get_current_user_optional", _fake_current_user)

    response = client.get("/attendance")
    assert response.status_code == 200
    assert "No accessible groupings" not in response.text
    # NR selector is present with the sample roll's CAA.
    assert 'name="nominal_roll_id"' in response.text
    assert "CAA 2024-01-01" in response.text
    # Default NR resolution picked the sample roll (no grouping involvement).
    assert "no Subunit-1 assignments" in response.text


@pytest.mark.asyncio
async def test_user_attendance_overlays_active_tagging_values(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_users,
    db_session,
    monkeypatch,
):
    """With the NR active for attendance, the roster shows effective (to_*)
    values, Copy Remarks (super-admin), and the changed-row highlight."""
    from parade_state.web import attendance as web_attendance
    from parade_state.models import Tagging, TaggingEntry, User

    admin_id = str(sample_users["admin"].id)

    # Build the NR's tagging with one remap for personnel[0] (Platoon 1 → 9).
    tagging = Tagging(
        label=None,
        nominal_roll_id=str(sample_nominal_roll.id),
        created_by=admin_id,
    )
    tagging.entries.append(
        TaggingEntry(
            personnel_id=str(sample_personnel[0].id),
            from_unit=sample_personnel[0].unit,
            from_sub_unit_1=sample_personnel[0].sub_unit_1,
            to_unit="Coy B",
            to_sub_unit_1="Platoon 9",
            to_sub_unit_2="Section 9",
        )
    )
    db_session.add(tagging)

    # Mark the NR active for attendance (tagging is applied automatically).
    sample_nominal_roll.attendance_active = True
    sample_nominal_roll.attendance_activated_by = admin_id
    db_session.add(sample_nominal_roll)
    await db_session.commit()

    # super_admin sees the whole roster including the tagged row.
    super_admin = User(
        email="super@example.com",
        name="Super Admin",
        role="super_admin",
        status="active",
    )
    db_session.add(super_admin)
    await db_session.commit()

    async def _fake_current_user(_request):
        return super_admin

    monkeypatch.setattr(web_attendance, "get_current_user_optional", _fake_current_user)

    response = client.get(
        "/attendance", params={"nominal_roll_id": str(sample_nominal_roll.id)}
    )
    assert response.status_code == 200
    # Save controls only render when the NR is active for attendance.
    assert "Save Attendance" in response.text
    # Copy Remarks is super-admin-only now that the admin page is gone.
    assert "Copy Remarks" in response.text
    assert "changed-row" in response.text
    # Sub-unit 2/3 columns are shown; effective values come from the
    # tagging entry, not the canonical row.
    assert "Sub-unit 2" in response.text
    assert "Sub-unit 3" in response.text
    assert "Coy B" in response.text
    assert "Platoon 9" in response.text
    assert "Section 9" in response.text


@pytest.mark.asyncio
async def test_user_attendance_copy_remarks_hidden_for_non_super_admins(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
    monkeypatch,
):
    """Copy Remarks is super-admin-only; other admins get Save alone."""
    from parade_state.web import attendance as web_attendance
    from parade_state.models import UserSubunitAssignment

    admin = sample_users["admin"]

    async def _fake_current_user(_request):
        return admin

    monkeypatch.setattr(web_attendance, "get_current_user_optional", _fake_current_user)

    # Grant an assignment so the roster is visible with the active NR.
    from parade_state.db import get_session_maker

    sm = get_session_maker()
    async with sm() as db:
        db.add(
            UserSubunitAssignment(
                user_id=str(admin.id),
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
    assert "Save Attendance" in response.text
    # The button (not the JS helper) is what's gated.
    assert 'onclick="copyRemarks()"' not in response.text


@pytest.mark.asyncio
async def test_user_attendance_subunit_filter_is_effective_aware(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_users,
    db_session,
    monkeypatch,
):
    """The sub-unit filter matches effective (tagging-overlaid) values."""
    from parade_state.web import attendance as web_attendance
    from parade_state.models import Tagging, TaggingEntry, User

    admin_id = str(sample_users["admin"].id)

    # Remap personnel[0] (Platoon 1) → Platoon 9.
    tagging = Tagging(
        label=None,
        nominal_roll_id=str(sample_nominal_roll.id),
        created_by=admin_id,
    )
    tagging.entries.append(
        TaggingEntry(
            personnel_id=str(sample_personnel[0].id),
            from_unit=sample_personnel[0].unit,
            from_sub_unit_1=sample_personnel[0].sub_unit_1,
            to_unit="Coy A",
            to_sub_unit_1="Platoon 9",
        )
    )
    db_session.add(tagging)
    sample_nominal_roll.attendance_active = True
    db_session.add(sample_nominal_roll)
    await db_session.commit()

    super_admin = User(
        email="super2@example.com",
        name="Super Admin",
        role="super_admin",
        status="active",
    )
    db_session.add(super_admin)
    await db_session.commit()

    async def _fake_current_user(_request):
        return super_admin

    monkeypatch.setattr(web_attendance, "get_current_user_optional", _fake_current_user)

    # Filter by the effective value: personnel[0] appears under Platoon 9,
    # not under their canonical Platoon 1.
    r9 = client.get(
        "/attendance",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "sub_unit_1": "Platoon 9",
        },
    )
    assert r9.status_code == 200
    assert "John Doe" in r9.text

    r1 = client.get(
        "/attendance",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "sub_unit_1": "Platoon 1",
        },
    )
    assert r1.status_code == 200
    assert "John Doe" not in r1.text
    assert "Jane Smith" in r1.text  # canonical Platoon 1, untagged
