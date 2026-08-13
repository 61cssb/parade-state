"""Tests for nominal_roll API endpoints (PATCH confirm, label, DELETE)."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models.csv_ingestion import NominalRoll
from tests.test_utils import assert_permission_denied


# ============================================================================
# PATCH /api/v1/nominal-rolls/{id} — confirm
# ============================================================================


@pytest.mark.asyncio
async def test_confirm_draft_nominal_roll(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_users,
):
    """Admin can confirm a draft nominal_roll."""
    draft_nominal_roll = NominalRoll(
        caa=date(2024, 5, 1),
        csv_hash="draft-hash-confirm",
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_nominal_roll)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/nominal-rolls/{draft_nominal_roll.id}",
        json={"status": "confirmed"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["confirmed_at"] is not None
    assert data["confirmed_by"] == "admin-user-id"


@pytest.mark.asyncio
async def test_confirm_already_confirmed_nominal_roll(
    client: TestClient, admin_token_headers: dict[str, str], sample_nominal_roll,
):
    """Cannot confirm an nominal_roll that is already confirmed."""
    response = client.patch(
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}",
        json={"status": "confirmed"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Only draft nominal rolls" in response.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_archived_nominal_roll(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_users,
):
    """Cannot confirm an archived nominal_roll."""
    archived_nominal_roll = NominalRoll(
        caa=date(2023, 1, 1),
        csv_hash="archived-hash-confirm",
        status="archived",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(archived_nominal_roll)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/nominal-rolls/{archived_nominal_roll.id}",
        json={"status": "confirmed"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Only draft nominal rolls" in response.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_non_existent_nominal_roll(
    client: TestClient, admin_token_headers: dict[str, str],
):
    """404 when confirming a non-existent nominal_roll."""
    response = client.patch(
        "/api/v1/nominal-rolls/does-not-exist",
        json={"status": "confirmed"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_confirm_nominal_roll_as_regular_user_forbidden(
    client: TestClient, user_token_headers: dict[str, str], sample_nominal_roll,
):
    """Regular users cannot confirm nominal_rolls."""
    assert_permission_denied(
        client,
        "patch",
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}",
        user_token_headers,
        expected_detail="Only admins and super admins",
        params={"user_id": "regular-user-id", "user_role": "user"},
        json_data={"status": "confirmed"},
    )


@pytest.mark.asyncio
async def test_revert_confirmed_nominal_roll_to_draft(
    client: TestClient, admin_token_headers: dict[str, str], sample_nominal_roll,
):
    """Admin can revert a confirmed nominal_roll back to draft (for testing)."""
    response = client.patch(
        f"/api/v1/nominal-rolls/{sample_nominal_roll.id}",
        json={"status": "draft"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert data["confirmed_at"] is None
    assert data["confirmed_by"] is None


@pytest.mark.asyncio
async def test_revert_draft_nominal_roll_fails(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_users,
):
    """Cannot revert an already-draft nominal_roll."""
    draft_nominal_roll = NominalRoll(
        caa=date(2024, 8, 1),
        csv_hash="draft-hash-revert",
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_nominal_roll)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/nominal-rolls/{draft_nominal_roll.id}",
        json={"status": "draft"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Only confirmed nominal rolls" in response.json()["detail"]


# ============================================================================
# DELETE /api/v1/nominal-rolls/{id}
# ============================================================================


@pytest.mark.asyncio
async def test_delete_draft_nominal_roll(
    client: TestClient, super_admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Super admin can delete a draft nominal_roll."""
    draft_nominal_roll = NominalRoll(
        caa=date(2024, 7, 1),
        csv_hash="draft-hash-delete",
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_nominal_roll)
    await db_session.commit()
    nominal_roll_id = str(draft_nominal_roll.id)

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
    """Super admin can delete a confirmed nominal_roll."""
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
async def test_delete_archived_nominal_roll(
    client: TestClient, super_admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Cannot delete an archived nominal_roll."""
    archived_nominal_roll = NominalRoll(
        caa=date(2022, 1, 1),
        csv_hash="archived-hash-delete",
        status="archived",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(archived_nominal_roll)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/nominal-rolls/{archived_nominal_roll.id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 400
    assert "draft or confirmed" in response.json()["detail"]


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
    sample_nominal_roll, sample_deployment, sample_attendance,
):
    """Deleting an nominal_roll cascade-deletes dependent data (verified via API)."""
    nominal_roll_id = str(sample_nominal_roll.id)
    deployment_id = str(sample_deployment.id)

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

    # Deployment cascade-deleted — no longer accessible
    dep_response = client.get(
        f"/api/v1/deployments/{deployment_id}",
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
        status="draft",
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
        status="draft",
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
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
        label="Shared Label",
    )
    nominal_roll_b = NominalRoll(
        caa=date(2024, 12, 1),
        csv_hash="label-hash-dup-b",
        status="draft",
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
        status="draft",
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
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
        label="Visible",
    )
    unlabeled = NominalRoll(
        caa=date(2025, 3, 1),
        csv_hash="label-hash-list-b",
        status="draft",
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
