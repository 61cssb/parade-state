"""Behavioral tests for scope-grant access (issues #4 and #28).

Covers: deny-by-default enforcement on attendance upsert and copy-remarks,
tagging-aware effective (unit, sub_unit_1), super_admin bypass, and the
super-admin grant CRUD endpoints (roster-validated, '*' wildcards).
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import UserSubunitAssignment

SUPER_ADMIN_SESSION = "super_admin"  # client_as shorthand for grant CRUD
ADMIN_SESSION = "admin"


# ============================================================================
# Assignment CRUD (super-admin only)
# ============================================================================


@pytest.mark.asyncio
async def test_grant_requires_super_admin(
    client: TestClient, client_as, sample_nominal_roll, sample_users
):
    """Non-super-admins cannot grant assignments (403)."""
    client = await client_as("admin")
    response = client.post(
        f"/api/v1/access-control/nominal-rolls/{sample_nominal_roll.id}"
        f"/users/{sample_users['user'].id}/subunit-assignments",
        json={"sub_unit_1": "Platoon 1"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Super admin access required"


@pytest.mark.asyncio
async def test_grant_then_list_assignment(
    client: TestClient, client_as, sample_nominal_roll, sample_users, sample_personnel
):
    """Super-admin can grant an assignment and list it back."""
    client = await client_as("super_admin")
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)

    response = client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        json={"sub_unit_1": "Platoon 1"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["sub_unit_1"] == "Platoon 1"
    assert created["unit"] == "*"

    # List for NR (super-admin sees all).
    client = await client_as("super_admin")
    response = client.get(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/subunit-assignments",
    )
    assert response.status_code == 200
    assert any(a["sub_unit_1"] == "Platoon 1" for a in response.json())


@pytest.mark.asyncio
async def test_grant_duplicate_409(
    client: TestClient, client_as, sample_nominal_roll, sample_users, sample_personnel
):
    """Granting the same (user, NR, sub_unit_1) twice returns 409."""
    client = await client_as("super_admin")
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)
    payload = {"sub_unit_1": "Platoon 1"}

    first = client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        json=payload,
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        json=payload,
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_revoke_assignment(
    client: TestClient, client_as, sample_nominal_roll, sample_users, sample_personnel
):
    """Super-admin can revoke an assignment."""
    client = await client_as("super_admin")
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)

    created = client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        json={"sub_unit_1": "Platoon 1"},
    ).json()

    response = client.delete(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}"
        f"/subunit-assignments/{created['id']}",
    )
    assert response.status_code == 200

    # List confirms it's gone.
    client = await client_as("super_admin")
    remaining = client.get(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/subunit-assignments",
    ).json()
    assert all(a["id"] != created["id"] for a in remaining)


@pytest.mark.asyncio
async def test_list_for_user_self_only(
    client: TestClient, client_as, sample_nominal_roll, sample_users,
    sample_personnel
):
    """A user can list their own assignments but not another user's."""
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)

    sa_client = await client_as("super_admin")
    sa_client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        json={"sub_unit_1": "Platoon 1"},
    )

    # Self: OK.
    client = await client_as(sample_users["user"])
    response = client.get(
        f"/api/v1/access-control/users/{user_id}/subunit-assignments",
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    # Other user: 403.
    response = client.get(
        f"/api/v1/access-control/users/{str(sample_users['admin'].id)}/subunit-assignments",
    )
    assert response.status_code == 403


# ============================================================================
# Enforcement on attendance upsert
# ============================================================================


@pytest.mark.asyncio
async def test_upsert_denied_without_assignment(
    client: TestClient,
    client_as,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """An admin with no assignment on the NR gets 403 on upsert."""
    client = await client_as(sample_users["admin"])
    today = date.today().isoformat()

    response = client.put(
        "/api/v1/attendance/upsert",
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
    assert response.status_code == 403
    assert "Platoon 1" in response.json()["detail"]  # personnel[0] is in Platoon 1


@pytest.mark.asyncio
async def test_upsert_allowed_with_matching_assignment(
    client: TestClient,
    client_as,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """An admin granted the right sub_unit_1 can upsert."""
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()

    # Grant Platoon 1 only (super-admin mints the grant).
    sa_client = await client_as("super_admin")
    sa_client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{admin_id}/subunit-assignments",
        json={"sub_unit_1": "Platoon 1"},
    )
    client = await client_as(sample_users["admin"])

    # Upsert for a Platoon 1 person → OK.
    response = client.put(
        "/api/v1/attendance/upsert",
        json={
            "nominal_roll_id": nr_id,
            "records": [
                {
                    "personnel_id": str(sample_personnel[0].id),  # Platoon 1
                    "date": today,
                    "status_am": "present",
                    "status_pm": "present",
                }
            ],
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_upsert_denied_for_unassigned_subunit(
    client: TestClient,
    client_as,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """An admin granted Platoon 1 cannot upsert for a Platoon 2 person."""
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()

    sa_client = await client_as("super_admin")
    sa_client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{admin_id}/subunit-assignments",
        json={"sub_unit_1": "Platoon 1"},
    )
    client = await client_as(sample_users["admin"])

    # personnel[2] is in Platoon 2 → 403.
    response = client.put(
        "/api/v1/attendance/upsert",
        json={
            "nominal_roll_id": nr_id,
            "records": [
                {
                    "personnel_id": str(sample_personnel[2].id),
                    "date": today,
                    "status_am": "present",
                    "status_pm": "present",
                }
            ],
        },
    )
    assert response.status_code == 403
    assert "Platoon 2" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upsert_super_admin_bypasses(
    client: TestClient,
    client_as,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
):
    """Super-admin can upsert for any subunit without assignments."""
    client = await client_as("super_admin")
    today = date.today().isoformat()
    response = client.put(
        "/api/v1/attendance/upsert",
        json={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "records": [
                {
                    "personnel_id": str(sample_personnel[2].id),  # Platoon 2
                    "date": today,
                    "status_am": "present",
                    "status_pm": "present",
                }
            ],
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_upsert_tagging_aware_effective_subunit(
    client: TestClient,
    client_as,
    db_session: AsyncSession,
    sample_nominal_roll,
    sample_personnel,
    sample_users,
):
    """Access follows the active tagging's remapped sub_unit_1, not the canonical one.

    personnel[2] canonically sits in Platoon 2. The NR's tagging remaps them
    to Platoon 1. With the NR active for attendance (tagging always applied),
    a user assigned only Platoon 1 should be allowed to upsert for
    personnel[2].
    """
    from parade_state.models import Tagging, TaggingEntry

    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()

    # Build a tagging that remaps personnel[2] → Platoon 1.
    tagging = Tagging(
        label="remap-p2-to-p1",
        nominal_roll_id=nr_id,
        created_by=admin_id,
    )
    tagging.entries.append(
        TaggingEntry(
            personnel_id=str(sample_personnel[2].id),
            from_unit="Coy A",
            from_sub_unit_1="Platoon 2",
            to_unit="Coy A",
            to_sub_unit_1="Platoon 1",
        )
    )
    db_session.add(tagging)

    # Mark the NR active for attendance (tagging is applied automatically).
    sample_nominal_roll.attendance_active = True
    sample_nominal_roll.attendance_activated_by = admin_id
    db_session.add(sample_nominal_roll)
    await db_session.commit()
    await db_session.refresh(tagging)

    # Grant admin only Platoon 1.
    sa_client = await client_as("super_admin")
    sa_client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{admin_id}/subunit-assignments",
        json={"sub_unit_1": "Platoon 1"},
    )

    # Upsert for personnel[2] (canonical Platoon 2, effective Platoon 1) → OK.
    client = await client_as(sample_users["admin"])
    response = client.put(
        "/api/v1/attendance/upsert",
        json={
            "nominal_roll_id": nr_id,
            "records": [
                {
                    "personnel_id": str(sample_personnel[2].id),
                    "date": today,
                    "status_am": "present",
                    "status_pm": "present",
                }
            ],
        },
    )
    assert response.status_code == 200


# ============================================================================
# Enforcement on copy-remarks
# ============================================================================


@pytest.mark.asyncio
async def test_copy_remarks_denied_without_assignment(
    client: TestClient,
    client_as,
    sample_nominal_roll,
    sample_attendance_scope,
    sample_attendance,
    sample_users,
):
    """copy-remarks returns 403 when the caller has no assignment on the NR."""
    client = await client_as(sample_users["admin"])
    today = date.today().isoformat()

    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "source_date": today,
            "source_slot": "am",
            "dest_date": today,
            "dest_slot": "pm",
        },
    )
    assert response.status_code == 403
