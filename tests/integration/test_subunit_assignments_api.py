"""Behavioral tests for Subunit-1 attendance access (issue #4 PR 2).

Covers: deny-by-default enforcement on attendance upsert and copy-remarks,
tagging-aware effective sub_unit_1, super_admin bypass, and the super-admin
assignment CRUD endpoints.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import UserSubunitAssignment

SUPER_ADMIN = {"user_id": "super-admin-test-id", "user_role": "super_admin"}
# CRUD endpoints use role-specific param names for the actor.
GRANT_SA = {"granted_by": "super-admin-test-id", "user_role": "super_admin"}
REVOKE_SA = {"revoked_by": "super-admin-test-id", "user_role": "super_admin"}


# ============================================================================
# Assignment CRUD (super-admin only)
# ============================================================================


@pytest.mark.asyncio
async def test_grant_requires_super_admin(
    client: TestClient, sample_nominal_roll, sample_users, admin_id
):
    """Non-super-admins cannot grant assignments (403)."""
    response = client.post(
        f"/api/v1/access-control/nominal-rolls/{sample_nominal_roll.id}"
        f"/users/{sample_users['user'].id}/subunit-assignments",
        params={"granted_by": admin_id, "user_role": "admin"},
        json={"sub_unit_1": "Platoon 1"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_grant_then_list_assignment(
    client: TestClient, sample_nominal_roll, sample_users
):
    """Super-admin can grant an assignment and list it back."""
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)

    response = client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        params=GRANT_SA,
        json={"sub_unit_1": "Platoon 1"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["sub_unit_1"] == "Platoon 1"

    # List for NR (super-admin sees all).
    response = client.get(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/subunit-assignments",
        params={"requesting_user_id": "super-admin-test-id", "requesting_user_role": "super_admin"},
    )
    assert response.status_code == 200
    assert any(a["sub_unit_1"] == "Platoon 1" for a in response.json())


@pytest.mark.asyncio
async def test_grant_duplicate_409(
    client: TestClient, sample_nominal_roll, sample_users
):
    """Granting the same (user, NR, sub_unit_1) twice returns 409."""
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)
    payload = {"sub_unit_1": "Platoon 1"}

    first = client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        params=GRANT_SA, json=payload,
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        params=GRANT_SA, json=payload,
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_revoke_assignment(
    client: TestClient, sample_nominal_roll, sample_users
):
    """Super-admin can revoke an assignment."""
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)

    created = client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        params=GRANT_SA, json={"sub_unit_1": "Platoon 1"},
    ).json()

    response = client.delete(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}"
        f"/subunit-assignments/{created['id']}",
        params=REVOKE_SA,
    )
    assert response.status_code == 200

    # List confirms it's gone.
    remaining = client.get(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/subunit-assignments",
        params={"requesting_user_id": "super-admin-test-id", "requesting_user_role": "super_admin"},
    ).json()
    assert all(a["id"] != created["id"] for a in remaining)


@pytest.mark.asyncio
async def test_list_for_user_self_only(
    client: TestClient, sample_nominal_roll, sample_users
):
    """A regular user can list their own assignments but not another user's."""
    user_id = str(sample_users["user"].id)
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)

    client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments",
        params=GRANT_SA, json={"sub_unit_1": "Platoon 1"},
    )

    # Self: OK.
    response = client.get(
        f"/api/v1/access-control/users/{user_id}/subunit-assignments",
        params={"requesting_user_id": user_id, "requesting_user_role": "user"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    # Other user: 403.
    response = client.get(
        f"/api/v1/access-control/users/{admin_id}/subunit-assignments",
        params={"requesting_user_id": user_id, "requesting_user_role": "user"},
    )
    assert response.status_code == 403


# ============================================================================
# Enforcement on attendance upsert
# ============================================================================


@pytest.mark.asyncio
async def test_upsert_denied_without_assignment(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """An admin with no assignment on the NR gets 403 on upsert."""
    regular_id = str(sample_users["user"].id)
    today = date.today().isoformat()

    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": regular_id, "user_role": "user"},
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
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """An admin granted the right sub_unit_1 can upsert."""
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()

    # Grant Platoon 1 only.
    client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{admin_id}/subunit-assignments",
        params=GRANT_SA, json={"sub_unit_1": "Platoon 1"},
    )

    # Upsert for a Platoon 1 person → OK.
    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "admin"},
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
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
):
    """An admin granted Platoon 1 cannot upsert for a Platoon 2 person."""
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()

    client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{admin_id}/subunit-assignments",
        params=GRANT_SA, json={"sub_unit_1": "Platoon 1"},
    )

    # personnel[2] is in Platoon 2 → 403.
    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "admin"},
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
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
):
    """Super-admin can upsert for any subunit without assignments."""
    today = date.today().isoformat()
    response = client.put(
        "/api/v1/attendance/upsert",
        params=SUPER_ADMIN,
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
    client.post(
        f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{admin_id}/subunit-assignments",
        params=GRANT_SA, json={"sub_unit_1": "Platoon 1"},
    )

    # Upsert for personnel[2] (canonical Platoon 2, effective Platoon 1) → OK.
    response = client.put(
        "/api/v1/attendance/upsert",
        params={"user_id": admin_id, "user_role": "admin"},
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
    sample_nominal_roll,
    sample_attendance_scope,
    sample_attendance,
    sample_users,
):
    """copy-remarks returns 403 when the caller has no assignment on the NR."""
    regular_id = str(sample_users["user"].id)
    today = date.today().isoformat()

    response = client.post(
        "/api/v1/attendance/copy-remarks",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "source_date": today,
            "source_slot": "am",
            "dest_date": today,
            "dest_slot": "pm",
            "user_id": regular_id,
            "user_role": "user",
        },
    )
    assert response.status_code == 403
