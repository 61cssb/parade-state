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
    values, per-row autosave (no Save button, no tagged-row highlight —
    issue 19), and the Copy Remarks modal (issue 20)."""
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
    # Autosave replaces the bulk Save flow; rows highlight only on failure.
    assert "Save Attendance" not in response.text
    assert "saveAttendance" not in response.text
    assert "onStatusChange" in response.text  # autosave hook on statuses
    # Yellow tagged-row highlight is gone from attendance (NR view keeps it).
    assert "changed-row" not in response.text
    # Copy Remarks modal is offered (write perms enforced server-side).
    assert "Copy Remarks" in response.text
    assert 'id="copy-modal"' in response.text
    # Sub-unit 2/3 columns are shown; effective values come from the
    # tagging entry, not the canonical row.
    assert "Sub-unit 2" in response.text
    assert "Sub-unit 3" in response.text
    assert "Coy B" in response.text
    assert "Platoon 9" in response.text
    assert "Section 9" in response.text


@pytest.mark.asyncio
async def test_user_attendance_copy_remarks_available_to_all_admins(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
    monkeypatch,
):
    """Copy Remarks is open to every admin (issue 20): the endpoint enforces
    sub-unit write access, so the button is no longer super-admin-only."""
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
    assert "Save Attendance" not in response.text
    # The button and modal render for plain admins too.
    assert "Copy Remarks" in response.text
    assert 'id="copy-modal"' in response.text


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


@pytest.mark.asyncio
async def test_attendance_lists_all_personnel_with_inpro_column(
    client: TestClient,
    sample_nominal_roll,
    sample_attendance_scope,
    sample_personnel,
    sample_users,
    db_session,
    monkeypatch,
):
    """Issue 33: the roster is everyone on the NR — deferred included —
    with a read-only Inpro Status column rendered before the status."""
    from parade_state.web import attendance as web_attendance
    from parade_state.models import User

    # John Doe → deferred, Jane Smith → inproed, Bob stays yet_to_inpro.
    sample_personnel[0].inpro_status = "deferred"
    sample_personnel[1].inpro_status = "inproed"
    db_session.add_all(sample_personnel[:2])
    await db_session.commit()

    super_admin = User(
        email="super-inpro@example.com",
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
    # Read-only Inpro Status column between Name and Status.
    assert "Inpro Status" in response.text
    # Everyone renders, each with their inpro label.
    assert "John Doe" in response.text  # deferred → still listed
    assert "Jane Smith" in response.text  # inproed
    assert "Bob Johnson" in response.text  # yet_to_inpro (default)
    assert "Deferred" in response.text
    assert "Inpro&#39;ed" in response.text or "Inpro'ed" in response.text
    assert "Yet to Inpro" in response.text
    # Single-session grid: one status column, one reason column.
    assert "AM Status" not in response.text
    assert "PM Status" not in response.text
    assert 'class="reason-select"' in response.text


@pytest.mark.asyncio
async def test_attendance_inpro_filter_hides_deferred(
    client: TestClient,
    sample_nominal_roll,
    sample_attendance_scope,
    sample_personnel,
    sample_users,
    db_session,
    monkeypatch,
):
    """The Inpro Status view filter narrows the roster (e.g. hide Deferred),
    and filtering is non-destructive: attendance records survive untouched."""
    from parade_state.web import attendance as web_attendance
    from parade_state.models import Attendance, User
    from parade_state.utils import utc_dt

    p = sample_personnel[0]
    record = Attendance(
        personnel_id=str(p.id),
        nominal_roll_id=str(sample_nominal_roll.id),
        date=utc_dt.utcnow().date(),
        status="present",
        remarks="marked earlier",
        created_by=str(sample_users["admin"].id),
        updated_by=str(sample_users["admin"].id),
    )
    db_session.add(record)

    # John Doe → deferred, Jane Smith → inproed.
    p.inpro_status = "deferred"
    sample_personnel[1].inpro_status = "inproed"
    db_session.add_all([p, sample_personnel[1]])
    await db_session.commit()

    super_admin = User(
        email="super-hidden@example.com",
        name="Super Admin",
        role="super_admin",
        status="active",
    )
    db_session.add(super_admin)
    await db_session.commit()

    async def _fake_current_user(_request):
        return super_admin

    monkeypatch.setattr(web_attendance, "get_current_user_optional", _fake_current_user)

    # Deferred filter: only John Doe.
    response = client.get(
        "/attendance",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "inpro_status": "deferred",
        },
    )
    assert response.status_code == 200
    assert "John Doe" in response.text
    assert "Jane Smith" not in response.text
    assert "Bob Johnson" not in response.text

    # Inpro'ed filter: only Jane Smith; John's record survives untouched.
    response = client.get(
        "/attendance",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "inpro_status": "inproed",
        },
    )
    assert response.status_code == 200
    assert "Jane Smith" in response.text
    assert "John Doe" not in response.text

    await db_session.refresh(record)
    assert record.status == "present"
    assert record.remarks == "marked earlier"


@pytest.mark.asyncio
async def test_frozen_day_banner_and_readonly_grid(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
    db_session,
    monkeypatch,
):
    """Issue 35: on a frozen day every role sees the banner (with the
    freeze timestamp); admins get a read-only grid (no inputs, no toggle),
    super-admins keep the editable grid and the freeze toggle."""
    from parade_state.web import attendance as web_attendance
    from parade_state.models import AttendanceFreeze, User, UserSubunitAssignment
    from parade_state.db import get_session_maker
    from parade_state.utils import utc_dt

    admin = sample_users["admin"]
    admin_id = str(admin.id)
    nr_id = str(sample_nominal_roll.id)

    # Grant an assignment so the roster renders, and freeze today.
    sm = get_session_maker()
    async with sm() as db:
        db.add(
            UserSubunitAssignment(
                user_id=admin_id,
                nominal_roll_id=nr_id,
                sub_unit_1="Platoon 1",
                created_by=admin_id,
            )
        )
        db.add(
            AttendanceFreeze(
                nominal_roll_id=nr_id,
                date=utc_dt.utcnow().date(),
                created_by=admin_id,
            )
        )
        await db.commit()

    async def _fake_current_user(_request):
        return admin

    monkeypatch.setattr(web_attendance, "get_current_user_optional", _fake_current_user)

    response = client.get("/attendance", params={"nominal_roll_id": nr_id})
    assert response.status_code == 200
    assert "Attendance for this day is frozen" in response.text
    assert "frozen at" in response.text  # banner names the freeze timestamp
    assert "read-only" in response.text
    # Admin grid is read-only: no inputs at all (so no autosave edges).
    # (The selectors below match element markup, not the autosave JS.)
    assert '<select class="status-select"' not in response.text
    assert '<select class="reason-select"' not in response.text
    assert 'onblur="onRemarksBlur(this)"' not in response.text
    # The toggle is super-admin-only.
    assert 'id="freeze-toggle"' not in response.text

    super_admin = User(
        email="super-freeze@example.com",
        name="Super Admin",
        role="super_admin",
        status="active",
    )
    db_session.add(super_admin)
    await db_session.commit()

    async def _fake_super_admin(_request):
        return super_admin

    monkeypatch.setattr(web_attendance, "get_current_user_optional", _fake_super_admin)

    response = client.get("/attendance", params={"nominal_roll_id": nr_id})
    assert response.status_code == 200
    assert "Attendance for this day is frozen" in response.text
    assert '<select class="status-select"' in response.text  # still editable
    assert 'id="freeze-toggle"' in response.text
    assert "Unfreeze day" in response.text
