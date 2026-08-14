"""Tests for nominal_roll API endpoints (attendance activation, label, DELETE)."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models.csv_ingestion import NominalRoll
from tests.test_utils import assert_permission_denied


# ============================================================================
# POST /api/v1/nominal-rolls/{id}/activate-attendance | deactivate-attendance
# ============================================================================


@pytest.mark.asyncio
async def test_activate_attendance_marks_nr_active(
    client: TestClient, super_admin_token_headers: dict[str, str],
    sample_nominal_roll,
):
    """Super admin can mark an NR active for attendance."""
    response = client.post(
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}/activate-attendance",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["attendance_active"] is True
    assert data["attendance_activated_at"] is not None
    assert data["attendance_activated_by"] == "super-admin-test-id"


@pytest.mark.asyncio
async def test_activate_attendance_auto_switches(
    client: TestClient, super_admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_nominal_roll, sample_users,
):
    """Activating a second NR deactivates the previously active one."""
    sample_nominal_roll.attendance_active = True
    db_session.add(sample_nominal_roll)

    other = NominalRoll(
        caa=date(2024, 9, 1),
        csv_hash="auto-switch-hash",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(other)
    await db_session.commit()

    response = client.post(
        f"/api/v1/nominal-rolls/{other.id}/activate-attendance",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert response.status_code == 200
    assert response.json()["attendance_active"] is True

    # The previously active NR was deactivated in the same action.
    await db_session.refresh(sample_nominal_roll)
    await db_session.refresh(other)
    assert sample_nominal_roll.attendance_active is False
    assert other.attendance_active is True


@pytest.mark.asyncio
async def test_deactivate_attendance(
    client: TestClient, super_admin_token_headers: dict[str, str],
    sample_nominal_roll,
):
    """Deactivate clears the active flag; activation stamp kept as history."""
    client.post(
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}/activate-attendance",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    response = client.post(
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}/deactivate-attendance",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["attendance_active"] is False
    assert data["attendance_activated_at"] is not None  # kept as history


@pytest.mark.asyncio
async def test_activate_attendance_requires_super_admin(
    client: TestClient, admin_token_headers: dict[str, str], sample_nominal_roll,
):
    """Admins cannot toggle attendance activation."""
    response = client.post(
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}/activate-attendance",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_activate_attendance_non_existent_404(
    client: TestClient, super_admin_token_headers: dict[str, str],
):
    """404 when activating a non-existent nominal_roll."""
    response = client.post(
        "/api/v1/nominal-rolls/does-not-exist/activate-attendance",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ============================================================================
# PATCH /api/v1/nominal-rolls/{id}
# ============================================================================


@pytest.mark.asyncio
async def test_update_nominal_roll_non_existent_404(
    client: TestClient, admin_token_headers: dict[str, str],
):
    """404 when updating a non-existent nominal_roll."""
    response = client.patch(
        "/api/v1/nominal-rolls/does-not-exist",
        json={"remarks": "x"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_nominal_roll_as_regular_user_forbidden(
    client: TestClient, user_token_headers: dict[str, str], sample_nominal_roll,
):
    """Regular users cannot update nominal_rolls."""
    assert_permission_denied(
        client,
        "patch",
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}",
        user_token_headers,
        expected_detail="Only admins and super admins",
        params={"user_id": "regular-user-id", "user_role": "user"},
        json_data={"remarks": "nope"},
    )


# ============================================================================
# DELETE /api/v1/nominal-rolls/{id}
# ============================================================================


@pytest.mark.asyncio
async def test_delete_nominal_roll(
    client: TestClient, super_admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Super admin can delete a nominal_roll (no status gating)."""
    doomed = NominalRoll(
        caa=date(2024, 7, 1),
        csv_hash="hash-delete",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(doomed)
    await db_session.commit()
    nominal_roll_id = str(doomed.id)

    response = client.delete(
        f"/api/v1/nominal-rolls/{nominal_roll_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 200
    assert "deleted" in response.json()["detail"]

    # Verify gone via API
    verify = client.get(
        f"/api/v1/nominal-rolls/{nominal_roll_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert verify.status_code == 404


@pytest.mark.asyncio
async def test_delete_confirmed_nominal_roll(
    client: TestClient, super_admin_token_headers: dict[str, str],
    sample_nominal_roll,
):
    """Super admin can delete the sample nominal_roll."""
    nominal_roll_id = str(sample_nominal_roll.id)

    response = client.delete(
        f"/api/v1/nominal-rolls/{nominal_roll_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 200
    assert "deleted" in response.json()["detail"]

    # Verify gone via API
    verify = client.get(
        f"/api/v1/nominal-rolls/{nominal_roll_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert verify.status_code == 404


@pytest.mark.asyncio
async def test_delete_nominal_roll_as_admin_forbidden(
    client: TestClient, admin_token_headers: dict[str, str], sample_nominal_roll,
):
    """Admins (non-super) cannot delete nominal_rolls."""
    response = client.delete(
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 403
    assert "super admins" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_non_existent_nominal_roll(
    client: TestClient, super_admin_token_headers: dict[str, str],
):
    """404 when deleting a non-existent nominal_roll."""
    response = client.delete(
        "/api/v1/nominal-rolls/does-not-exist",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_nominal_roll_cascades(
    client: TestClient, super_admin_token_headers: dict[str, str],
    sample_nominal_roll, sample_grouping, sample_attendance,
):
    """Deleting an nominal_roll cascade-deletes dependent data (verified via API)."""
    nominal_roll_id = str(sample_nominal_roll.id)
    grouping_id = str(sample_grouping.id)

    response = client.delete(
        f"/api/v1/nominal-rolls/{nominal_roll_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert response.status_code == 200

    # NominalRoll is gone
    nominal_roll_response = client.get(
        f"/api/v1/nominal-rolls/{nominal_roll_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert nominal_roll_response.status_code == 404

    # Grouping cascade-deleted — no longer accessible
    dep_response = client.get(
        f"/api/v1/groupings/{grouping_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert dep_response.status_code == 404


# ============================================================================
# PATCH /api/v1/nominal-rolls/{id} — label
# ============================================================================


@pytest.mark.asyncio
async def test_update_nominal_roll_label(
    client: TestClient, admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Admin can set a label on an nominal_roll; response and GET reflect it."""
    draft_nominal_roll = NominalRoll(
        caa=date(2024, 9, 1),
        csv_hash="label-hash-set",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_nominal_roll)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/nominal-rolls/{draft_nominal_roll.id}",
        json={"label": "Q1 Roster"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["label"] == "Q1 Roster"

    # GET reflects the change
    get_resp = client.get(
        f"/api/v1/nominal-rolls/{draft_nominal_roll.id}",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["label"] == "Q1 Roster"


@pytest.mark.asyncio
async def test_update_nominal_roll_label_strips_whitespace(
    client: TestClient, admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Label is stripped before storage."""
    draft_nominal_roll = NominalRoll(
        caa=date(2024, 10, 1),
        csv_hash="label-hash-strip",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_nominal_roll)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/nominal-rolls/{draft_nominal_roll.id}",
        json={"label": "  Padded  "},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["label"] == "Padded"


@pytest.mark.asyncio
async def test_update_nominal_roll_label_duplicate_rejected(
    client: TestClient, admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Setting a label that's already in use returns 409."""
    nominal_roll_a = NominalRoll(
        caa=date(2024, 11, 1),
        csv_hash="label-hash-dup-a",
        uploaded_by=str(sample_users["admin"].id),
        label="Shared Label",
    )
    nominal_roll_b = NominalRoll(
        caa=date(2024, 12, 1),
        csv_hash="label-hash-dup-b",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add_all([nominal_roll_a, nominal_roll_b])
    await db_session.commit()

    response = client.patch(
        f"/api/v1/nominal-rolls/{nominal_roll_b.id}",
        json={"label": "Shared Label"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 409
    assert "already in use" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_nominal_roll_label_empty_rejected(
    client: TestClient, admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Empty/whitespace label fails schema validation (422)."""
    draft_nominal_roll = NominalRoll(
        caa=date(2025, 1, 1),
        csv_hash="label-hash-empty",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_nominal_roll)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/nominal-rolls/{draft_nominal_roll.id}",
        json={"label": "   "},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_nominal_rolls_includes_label(
    client: TestClient, admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """List endpoint returns the label field (null when unset)."""
    labeled = NominalRoll(
        caa=date(2025, 2, 1),
        csv_hash="label-hash-list-a",
        uploaded_by=str(sample_users["admin"].id),
        label="Visible",
    )
    unlabeled = NominalRoll(
        caa=date(2025, 3, 1),
        csv_hash="label-hash-list-b",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add_all([labeled, unlabeled])
    await db_session.commit()

    response = client.get(
        "/api/v1/nominal-rolls",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()}
    assert items[labeled.id]["label"] == "Visible"
    assert items[unlabeled.id]["label"] is None
