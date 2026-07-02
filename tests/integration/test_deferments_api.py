"""Behavioral tests for deferments API endpoints.

Covers: super_admin-only authorization, snapshot-on-create, and the
callup_status transition rules (Approved → Deferred; Approved → non-neutral
reverts to Called Up; Not called up / Do not call up leave callup unchanged).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import Deferment, Personnel


# Common query params reused across endpoints.
SUPER_ADMIN_PARAMS = {"user_id": "super-admin-test-id", "user_role": "super_admin"}
ADMIN_PARAMS = {"user_id": "admin-user-id", "user_role": "admin"}
USER_PARAMS = {"user_id": "regular-user-id", "user_role": "user"}


# ============================================================================
# Authorization
# ============================================================================


@pytest.mark.asyncio
async def test_admin_role_cannot_list_deferments(
    client: TestClient, admin_token_headers, sample_personnel
):
    """admin role is rejected — super_admin only."""
    response = client.get(
        "/api/v1/deferments",
        headers=admin_token_headers,
        params=ADMIN_PARAMS,
    )
    assert response.status_code == 403
    assert "super admins" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_regular_user_cannot_create_deferment(
    client: TestClient, user_token_headers, sample_personnel
):
    """user role is rejected."""
    response = client.post(
        "/api/v1/deferments",
        headers=user_token_headers,
        params=USER_PARAMS,
        json={"personnel_id": str(sample_personnel[0].id), "reason": "Medical Grounds"},
    )
    assert response.status_code == 403


# ============================================================================
# Create
# ============================================================================


@pytest.mark.asyncio
async def test_create_deferment_snapshots_rank_and_subunit(
    client: TestClient, super_admin_token_headers, sample_personnel
):
    """POST snapshotted rank_name + sub_unit from personnel; status defaults to Pending action."""
    person = sample_personnel[0]  # rank=PTE, full_name=John Doe, sub_unit_1=Platoon 1
    response = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(person.id), "reason": "Medical Grounds"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["rank_name"] == "PTE John Doe"
    assert data["sub_unit"] == "Platoon 1"
    assert data["status"] == "Pending action"
    assert data["reason"] == "Medical Grounds"
    assert data["personnel_id"] == str(person.id)


@pytest.mark.asyncio
async def test_create_deferment_for_nonexistent_personnel_404(
    client: TestClient, super_admin_token_headers
):
    response = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": "nonexistent-id", "reason": "Work"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_deferment_for_archived_personnel_400(
    client: TestClient, super_admin_token_headers, sample_personnel, db_session: AsyncSession
):
    person = sample_personnel[0]
    person.status = "archived"
    await db_session.commit()

    response = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(person.id), "reason": "Work"},
    )
    assert response.status_code == 400
    assert "non-active" in response.json()["detail"]


# ============================================================================
# Callup transition rules
# ============================================================================


async def _refresh_personnel(db_session: AsyncSession, person_id: str) -> Personnel:
    """Re-fetch a personnel row to read the latest callup_status.

    Uses ``populate_existing`` so SQLAlchemy overwrites the identity-map cached
    object with the committed-by-API row from a different session.
    """
    db_session.expire_all()
    result = await db_session.execute(
        select(Personnel)
        .where(Personnel.id == person_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_approve_deferment_sets_personnel_deferred(
    client: TestClient, super_admin_token_headers, sample_personnel, db_session: AsyncSession
):
    person = sample_personnel[0]
    create = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(person.id), "reason": "Medical Grounds"},
    )
    deferment_id = create.json()["id"]

    response = client.patch(
        f"/api/v1/deferments/{deferment_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"status": "Approved"},
    )
    assert response.status_code == 200

    refreshed = await _refresh_personnel(db_session, str(person.id))
    assert refreshed.callup_status == "Deferred"


@pytest.mark.asyncio
@pytest.mark.parametrize("new_status", ["Withdrawn", "Rejected", "To Resubmit"])
async def test_approved_to_non_neutral_reverts_to_called_up(
    client: TestClient, super_admin_token_headers, sample_personnel, db_session: AsyncSession,
    new_status,
):
    person = sample_personnel[0]
    deferment_id = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(person.id), "reason": "Work"},
    ).json()["id"]

    # Approve first
    client.patch(
        f"/api/v1/deferments/{deferment_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"status": "Approved"},
    )
    assert (await _refresh_personnel(db_session, str(person.id))).callup_status == "Deferred"

    # Now move away from Approved to a non-neutral status
    client.patch(
        f"/api/v1/deferments/{deferment_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"status": new_status},
    )
    assert (await _refresh_personnel(db_session, str(person.id))).callup_status == "Called Up"


@pytest.mark.asyncio
@pytest.mark.parametrize("neutral_status", ["Not called up", "Do not call up"])
async def test_approved_to_neutral_status_leaves_callup_unchanged(
    client: TestClient, super_admin_token_headers, sample_personnel, db_session: AsyncSession,
    neutral_status,
):
    person = sample_personnel[0]
    deferment_id = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(person.id), "reason": "Work"},
    ).json()["id"]

    client.patch(
        f"/api/v1/deferments/{deferment_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"status": "Approved"},
    )
    assert (await _refresh_personnel(db_session, str(person.id))).callup_status == "Deferred"

    client.patch(
        f"/api/v1/deferments/{deferment_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"status": neutral_status},
    )
    # Stays Deferred — neutral statuses don't touch callup_status
    assert (await _refresh_personnel(db_session, str(person.id))).callup_status == "Deferred"


@pytest.mark.asyncio
async def test_transition_between_non_approved_statuses_no_callup_change(
    client: TestClient, super_admin_token_headers, sample_personnel, db_session: AsyncSession
):
    person = sample_personnel[0]
    deferment_id = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(person.id), "reason": "Work"},
    ).json()["id"]

    for new_status in ["To Resubmit", "Withdrawn", "Rejected"]:
        client.patch(
            f"/api/v1/deferments/{deferment_id}",
            headers=super_admin_token_headers,
            params=SUPER_ADMIN_PARAMS,
            json={"status": new_status},
        )
        assert (
            (await _refresh_personnel(db_session, str(person.id))).callup_status
            == "Called Up"
        )


# ============================================================================
# Delete
# ============================================================================


@pytest.mark.asyncio
async def test_delete_approved_deferment_reverts_callup(
    client: TestClient, super_admin_token_headers, sample_personnel, db_session: AsyncSession
):
    person = sample_personnel[0]
    deferment_id = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(person.id), "reason": "Work"},
    ).json()["id"]

    client.patch(
        f"/api/v1/deferments/{deferment_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"status": "Approved"},
    )
    assert (await _refresh_personnel(db_session, str(person.id))).callup_status == "Deferred"

    response = client.delete(
        f"/api/v1/deferments/{deferment_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
    )
    assert response.status_code == 200
    assert (await _refresh_personnel(db_session, str(person.id))).callup_status == "Called Up"


@pytest.mark.asyncio
async def test_delete_non_approved_deferment_no_callup_change(
    client: TestClient, super_admin_token_headers, sample_personnel, db_session: AsyncSession
):
    person = sample_personnel[0]
    deferment_id = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(person.id), "reason": "Work"},
    ).json()["id"]
    # Status stays "Pending action" → delete should not change callup_status
    before = (await _refresh_personnel(db_session, str(person.id))).callup_status

    response = client.delete(
        f"/api/v1/deferments/{deferment_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
    )
    assert response.status_code == 200
    assert (await _refresh_personnel(db_session, str(person.id))).callup_status == before


# ============================================================================
# List filters
# ============================================================================


@pytest.mark.asyncio
async def test_list_filters_by_status(
    client: TestClient, super_admin_token_headers, sample_personnel
):
    p1, p2 = sample_personnel[0], sample_personnel[1]
    client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(p1.id), "reason": "Work"},
    )
    approved_id = client.post(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"personnel_id": str(p2.id), "reason": "Medical Grounds"},
    ).json()["id"]
    client.patch(
        f"/api/v1/deferments/{approved_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"status": "Approved"},
    )

    response = client.get(
        "/api/v1/deferments",
        headers=super_admin_token_headers,
        params={**SUPER_ADMIN_PARAMS, "status": "Approved"},
    )
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["status"] == "Approved"
