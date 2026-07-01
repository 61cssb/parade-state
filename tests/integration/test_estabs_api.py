"""Tests for estab API endpoints (PATCH confirm + DELETE)."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models.csv_ingestion import Estab
from tests.test_utils import assert_permission_denied


# ============================================================================
# PATCH /api/v1/estabs/{id} — confirm
# ============================================================================


@pytest.mark.asyncio
async def test_confirm_draft_estab(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_users,
):
    """Admin can confirm a draft estab."""
    draft_estab = Estab(
        caa=date(2024, 5, 1),
        csv_hash="draft-hash-confirm",
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_estab)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/estabs/{draft_estab.id}",
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
async def test_confirm_already_confirmed_estab(
    client: TestClient, admin_token_headers: dict[str, str], sample_estab,
):
    """Cannot confirm an estab that is already confirmed."""
    response = client.patch(
        f"/api/v1/estabs/{sample_estab.id}",
        json={"status": "confirmed"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Only draft estabs" in response.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_archived_estab(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_users,
):
    """Cannot confirm an archived estab."""
    archived_estab = Estab(
        caa=date(2023, 1, 1),
        csv_hash="archived-hash-confirm",
        status="archived",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(archived_estab)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/estabs/{archived_estab.id}",
        json={"status": "confirmed"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Only draft estabs" in response.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_non_existent_estab(
    client: TestClient, admin_token_headers: dict[str, str],
):
    """404 when confirming a non-existent estab."""
    response = client.patch(
        "/api/v1/estabs/does-not-exist",
        json={"status": "confirmed"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_confirm_estab_as_regular_user_forbidden(
    client: TestClient, user_token_headers: dict[str, str], sample_estab,
):
    """Regular users cannot confirm estabs."""
    assert_permission_denied(
        client,
        "patch",
        f"/api/v1/estabs/{sample_estab.id}",
        user_token_headers,
        expected_detail="Only admins and super admins",
        params={"user_id": "regular-user-id", "user_role": "user"},
        json_data={"status": "confirmed"},
    )


@pytest.mark.asyncio
async def test_revert_confirmed_estab_to_draft(
    client: TestClient, admin_token_headers: dict[str, str], sample_estab,
):
    """Admin can revert a confirmed estab back to draft (for testing)."""
    response = client.patch(
        f"/api/v1/estabs/{sample_estab.id}",
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
async def test_revert_draft_estab_fails(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_users,
):
    """Cannot revert an already-draft estab."""
    draft_estab = Estab(
        caa=date(2024, 8, 1),
        csv_hash="draft-hash-revert",
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_estab)
    await db_session.commit()

    response = client.patch(
        f"/api/v1/estabs/{draft_estab.id}",
        json={"status": "draft"},
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "Only confirmed estabs" in response.json()["detail"]


# ============================================================================
# DELETE /api/v1/estabs/{id}
# ============================================================================


@pytest.mark.asyncio
async def test_delete_draft_estab(
    client: TestClient, super_admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Super admin can delete a draft estab."""
    draft_estab = Estab(
        caa=date(2024, 7, 1),
        csv_hash="draft-hash-delete",
        status="draft",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(draft_estab)
    await db_session.commit()
    estab_id = str(draft_estab.id)

    response = client.delete(
        f"/api/v1/estabs/{estab_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 200
    assert "deleted" in response.json()["detail"]

    # Verify gone via API
    verify = client.get(
        f"/api/v1/estabs/{estab_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert verify.status_code == 404


@pytest.mark.asyncio
async def test_delete_confirmed_estab(
    client: TestClient, super_admin_token_headers: dict[str, str],
    sample_estab,
):
    """Super admin can delete a confirmed estab."""
    estab_id = str(sample_estab.id)

    response = client.delete(
        f"/api/v1/estabs/{estab_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 200
    assert "deleted" in response.json()["detail"]

    # Verify gone via API
    verify = client.get(
        f"/api/v1/estabs/{estab_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert verify.status_code == 404


@pytest.mark.asyncio
async def test_delete_archived_estab(
    client: TestClient, super_admin_token_headers: dict[str, str],
    db_session: AsyncSession, sample_users,
):
    """Cannot delete an archived estab."""
    archived_estab = Estab(
        caa=date(2022, 1, 1),
        csv_hash="archived-hash-delete",
        status="archived",
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(archived_estab)
    await db_session.commit()

    response = client.delete(
        f"/api/v1/estabs/{archived_estab.id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 400
    assert "draft or confirmed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_estab_as_admin_forbidden(
    client: TestClient, admin_token_headers: dict[str, str], sample_estab,
):
    """Admins (non-super) cannot delete estabs."""
    response = client.delete(
        f"/api/v1/estabs/{sample_estab.id}",
        headers=admin_token_headers,
        params={"user_id": "admin-user-id", "user_role": "admin"},
    )

    assert response.status_code == 403
    assert "super admins" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_non_existent_estab(
    client: TestClient, super_admin_token_headers: dict[str, str],
):
    """404 when deleting a non-existent estab."""
    response = client.delete(
        "/api/v1/estabs/does-not-exist",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_estab_cascades(
    client: TestClient, super_admin_token_headers: dict[str, str],
    sample_estab, sample_deployment, sample_attendance_records,
):
    """Deleting an estab cascade-deletes dependent data (verified via API)."""
    estab_id = str(sample_estab.id)
    deployment_id = str(sample_deployment.id)

    response = client.delete(
        f"/api/v1/estabs/{estab_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert response.status_code == 200

    # Estab is gone
    estab_response = client.get(
        f"/api/v1/estabs/{estab_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert estab_response.status_code == 404

    # Deployment cascade-deleted — no longer accessible
    dep_response = client.get(
        f"/api/v1/deployments/{deployment_id}",
        headers=super_admin_token_headers,
        params={"user_id": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert dep_response.status_code == 404
