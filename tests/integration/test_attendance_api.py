"""Behavioral tests for the reworked attendance API.

Covers the NR/Tagging-scoped AM/PM attendance model: active-scope gating,
bulk + per-row upsert, list, copy-remarks (explicit source/destination,
issue 20), and the scope-activation endpoint.
"""

from datetime import date, timedelta

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
    """copy-remarks returns the documented shape (explicit source/dest)."""
    today = date.today().isoformat()
    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "source_date": today,
            "source_slot": "am",
            "dest_date": today,
            "dest_slot": "pm",
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_slot"] == "am"
    assert body["dest_slot"] == "pm"
    assert body["updated"] + body["skipped"] >= 1


@pytest.mark.asyncio
async def test_copy_remarks_refuses_when_not_active(
    client: TestClient, sample_nominal_roll, admin_id
):
    """copy-remarks refuses (400) when the NR isn't active for attendance."""
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "source_date": yesterday,
            "source_slot": "pm",
            "dest_date": today,
            "dest_slot": "am",
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    assert response.status_code == 400


# ============================================================================
# Copy Remarks: explicit source/destination (issue 20)
# ============================================================================


async def _make_super_admin(db_session):
    from parade_state.models import User

    sa = User(
        email="copy-sa@example.com", name="Super Admin", role="super_admin",
        status="active",
    )
    db_session.add(sa)
    await db_session.commit()
    return sa


@pytest.mark.asyncio
async def test_copy_remarks_rejects_same_source_and_destination(
    client: TestClient,
    sample_nominal_roll,
    sample_attendance_scope,
    admin_id,
):
    """Source and destination date+slot must differ (400 otherwise)."""
    today = date.today().isoformat()
    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "source_date": today,
            "source_slot": "pm",
            "dest_date": today,
            "dest_slot": "pm",
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    assert response.status_code == 400
    assert "must differ" in response.json()["detail"]


@pytest.mark.asyncio
async def test_copy_remarks_prev_day_pm_to_today_am(
    client: TestClient,
    db_session,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """Yesterday's PM remarks land in today's AM: rows with a source remark
    are updated (destination rows created on demand with snapshots), blank
    or missing sources are skipped and touch nothing."""
    from sqlalchemy import select

    from parade_state.models import Attendance

    sa = await _make_super_admin(db_session)
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today()
    yesterday = today - timedelta(days=1)

    # p0 has a yesterday PM remark; p1 has a yesterday row but blank PM;
    # p2 (Officer, Platoon 2) has no attendance at all.
    db_session.add(
        Attendance(
            personnel_id=str(sample_personnel[0].id),
            nominal_roll_id=nr_id,
            date=yesterday,
            status_am="present",
            remarks_pm="On MC",
            created_by=admin_id,
            updated_by=admin_id,
        )
    )
    db_session.add(
        Attendance(
            personnel_id=str(sample_personnel[1].id),
            nominal_roll_id=nr_id,
            date=yesterday,
            status_am="present",
            created_by=admin_id,
            updated_by=admin_id,
        )
    )
    await db_session.commit()

    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": nr_id,
            "source_date": yesterday.isoformat(),
            "source_slot": "pm",
            "dest_date": today.isoformat(),
            "dest_slot": "am",
            "user_id": str(sa.id),
            "user_role": "super_admin",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 1
    assert body["skipped"] == 2

    rows = (
        (
            await db_session.execute(
                select(Attendance).where(
                    Attendance.nominal_roll_id == nr_id,
                    Attendance.date == today,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # only p0 got a destination row
    row = rows[0]
    assert row.personnel_id == str(sample_personnel[0].id)
    assert row.remarks_am == "On MC"
    assert row.status_am == "absent"  # created with defaults
    assert row.sub_unit_1_snapshot == sample_personnel[0].sub_unit_1


@pytest.mark.asyncio
async def test_copy_remarks_same_day_am_to_pm_overwrites(
    client: TestClient,
    db_session,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """A same-day AM → PM copy overwrites the existing PM remark."""
    from parade_state.models import Attendance

    sa = await _make_super_admin(db_session)
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today()

    target = Attendance(
        personnel_id=str(sample_personnel[0].id),
        nominal_roll_id=nr_id,
        date=today,
        status_am="present",
        remarks_am="Duty",
        remarks_pm="stale remark",
        created_by=admin_id,
        updated_by=admin_id,
    )
    db_session.add(target)
    await db_session.commit()

    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": nr_id,
            "source_date": today.isoformat(),
            "source_slot": "am",
            "dest_date": today.isoformat(),
            "dest_slot": "pm",
            "user_id": str(sa.id),
            "user_role": "super_admin",
        },
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 1

    await db_session.refresh(target)
    assert target.remarks_pm == "Duty"


@pytest.mark.asyncio
async def test_copy_remarks_subunit_filter_scopes_the_copy(
    client: TestClient,
    db_session,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """sub_unit_1 narrows the copy (the page's view filter): with
    Platoon 1 selected, the Platoon 2 officer is untouched — even for a
    super-admin."""
    from sqlalchemy import select

    from parade_state.models import Attendance

    sa = await _make_super_admin(db_session)
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today()

    # Everyone has a yesterday PM remark.
    for p in sample_personnel:
        db_session.add(
            Attendance(
                personnel_id=str(p.id),
                nominal_roll_id=nr_id,
                date=today - timedelta(days=1),
                status_am="present",
                remarks_pm="note " + p.full_name,
                created_by=admin_id,
                updated_by=admin_id,
            )
        )
    await db_session.commit()

    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": nr_id,
            "source_date": (today - timedelta(days=1)).isoformat(),
            "source_slot": "pm",
            "dest_date": today.isoformat(),
            "dest_slot": "am",
            "sub_unit_1": "Platoon 1",
            "user_id": str(sa.id),
            "user_role": "super_admin",
        },
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 2  # p0 + p1 only

    today_pids = {
        r.personnel_id
        for r in (
            await db_session.execute(
                select(Attendance.personnel_id).where(
                    Attendance.nominal_roll_id == nr_id,
                    Attendance.date == today,
                )
            )
        ).all()
    }
    assert str(sample_personnel[2].id) not in today_pids  # Platoon 2 skipped


@pytest.mark.asyncio
async def test_copy_remarks_filter_matches_effective_subunit(
    client: TestClient,
    db_session,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """The sub_unit_1 filter is tagging-aware: the officer remapped into
    Platoon 9 is matched by Platoon 9, not by his canonical Platoon 2."""
    from sqlalchemy import select

    from parade_state.models import Attendance, Tagging, TaggingEntry

    sa = await _make_super_admin(db_session)
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today()
    officer = sample_personnel[2]  # canonical Platoon 2

    db_session.add(
        Attendance(
            personnel_id=str(officer.id),
            nominal_roll_id=nr_id,
            date=today - timedelta(days=1),
            status_am="present",
            remarks_pm="moved remark",
            created_by=admin_id,
            updated_by=admin_id,
        )
    )
    tagging = Tagging(label=None, nominal_roll_id=nr_id, created_by=admin_id)
    tagging.entries.append(
        TaggingEntry(
            personnel_id=str(officer.id),
            to_unit="Coy A",
            to_sub_unit_1="Platoon 9",
        )
    )
    db_session.add(tagging)
    await db_session.commit()

    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": nr_id,
            "source_date": (today - timedelta(days=1)).isoformat(),
            "source_slot": "pm",
            "dest_date": today.isoformat(),
            "dest_slot": "am",
            "sub_unit_1": "Platoon 9",
            "user_id": str(sa.id),
            "user_role": "super_admin",
        },
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 1

    row = (
        await db_session.execute(
            select(Attendance).where(
                Attendance.personnel_id == str(officer.id),
                Attendance.date == today,
            )
        )
    ).scalar_one()
    assert row.remarks_am == "moved remark"


@pytest.mark.asyncio
async def test_copy_remarks_allows_earlier_destination(
    client: TestClient,
    db_session,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """Copying to an earlier session is allowed server-side (the
    probably-a-mistake warning is client-side only)."""
    from parade_state.models import Attendance

    sa = await _make_super_admin(db_session)
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today()

    source = Attendance(
        personnel_id=str(sample_personnel[0].id),
        nominal_roll_id=nr_id,
        date=today,
        status_am="present",
        remarks_am="earlier copy",
        created_by=admin_id,
        updated_by=admin_id,
    )
    db_session.add(source)
    await db_session.commit()

    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": nr_id,
            "source_date": today.isoformat(),
            "source_slot": "am",
            "dest_date": (today - timedelta(days=1)).isoformat(),
            "dest_slot": "pm",
            "user_id": str(sa.id),
            "user_role": "super_admin",
        },
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 1


# ============================================================================
# Per-row autosave (issue 19): single-record upsert
# ============================================================================


@pytest.mark.asyncio
async def test_per_row_upsert_single_record(
    client: TestClient,
    db_session,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
    admin_subunit_assignment,
):
    """The autosave payload (one record per PUT) persists, and a caller
    without subunit assignments is refused."""
    from sqlalchemy import select

    from parade_state.models import Attendance, User

    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()

    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "admin"},
        json={
            "nominal_roll_id": nr_id,
            "records": [
                {
                    "personnel_id": str(sample_personnel[0].id),
                    "date": today,
                    "status_am": "late",
                    "remarks_am": "arrived 0900",
                    "status_pm": "present",
                }
            ],
        },
    )
    assert response.status_code == 200
    row = (
        await db_session.execute(
            select(Attendance).where(
                Attendance.personnel_id == str(sample_personnel[0].id)
            )
        )
    ).scalar_one()
    assert row.status_am == "late"
    assert row.remarks_am == "arrived 0900"

    # Auth: an admin with no assignments gets 403 on the same payload shape.
    outsider = User(
        email="noaccess@example.com", name="No Access", role="admin", status="active"
    )
    db_session.add(outsider)
    await db_session.commit()

    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": str(outsider.id), "user_role": "admin"},
        json={
            "nominal_roll_id": nr_id,
            "records": [
                {
                    "personnel_id": str(sample_personnel[1].id),
                    "date": today,
                    "status_am": "present",
                    "status_pm": "present",
                }
            ],
        },
    )
    assert response.status_code == 403
