"""Tests for grouping personnel exclusion API endpoints."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import Grouping, GroupingUserAccess
from parade_state.utils import utc_dt


async def _make_draft_grouping(db_session: AsyncSession, nominal_roll_id: str, admin_id: str) -> Grouping:
    """Helper: create a draft grouping + admin access grant."""
    grouping = Grouping(
        name="Draft Test Grouping",
        nominal_roll_id=nominal_roll_id,
        mode="standard",
        valid_from=utc_dt.db_utcnow() + timedelta(days=1),
        valid_until=utc_dt.db_utcnow() + timedelta(days=30),
        created_by=admin_id,
    )
    db_session.add(grouping)
    await db_session.commit()

    access = GroupingUserAccess(
        user_id=admin_id,
        grouping_id=str(grouping.id),
        granted_by=admin_id,
    )
    db_session.add(access)
    await db_session.commit()

    return grouping


# ============================================================================
# POST /api/v1/groupings/{id}/exclusions — exclude
# ============================================================================


@pytest.mark.asyncio
async def test_exclude_personnel(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_nominal_roll, sample_personnel, sample_users,
):
    """Admin can exclude a personnel from a draft grouping."""
    admin_id = str(sample_users["admin"].id)
    grouping = await _make_draft_grouping(db_session, str(sample_nominal_roll.id), admin_id)
    personnel_id = str(sample_personnel[0].id)

    response = client.post(
        f"/api/v1/groupings/{grouping.id}/exclusions",
        json={"personnel_id": personnel_id},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 201
    assert "excluded" in response.json()["detail"].lower()

    # Verify personnel no longer appears in grouping listing
    list_response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(grouping.id),
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    assert list_response.status_code == 200
    ids = [p["id"] for p in list_response.json()]
    assert personnel_id not in ids


@pytest.mark.asyncio
async def test_exclude_personnel_idempotent(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_nominal_roll, sample_personnel, sample_users,
):
    """Excluding an already-excluded personnel is idempotent."""
    admin_id = str(sample_users["admin"].id)
    grouping = await _make_draft_grouping(db_session, str(sample_nominal_roll.id), admin_id)
    personnel_id = str(sample_personnel[0].id)

    response1 = client.post(
        f"/api/v1/groupings/{grouping.id}/exclusions",
        json={"personnel_id": personnel_id},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )
    assert response1.status_code == 201

    response2 = client.post(
        f"/api/v1/groupings/{grouping.id}/exclusions",
        json={"personnel_id": personnel_id},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )
    assert response2.status_code == 201
    assert "already excluded" in response2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_exclude_personnel_as_regular_user_forbidden(
    client: TestClient, user_token_headers: dict[str, str], db_session: AsyncSession,
    sample_nominal_roll, sample_personnel, sample_users,
):
    """Regular users cannot exclude personnel from a grouping."""
    admin_id = str(sample_users["admin"].id)
    grouping = await _make_draft_grouping(db_session, str(sample_nominal_roll.id), admin_id)

    response = client.post(
        f"/api/v1/groupings/{grouping.id}/exclusions",
        json={"personnel_id": str(sample_personnel[0].id)},
        headers=user_token_headers,
        params={"user_id": str(sample_users["user"].id), "user_role": "user"},
    )
    assert response.status_code == 403
    assert "super admins" in response.json()["detail"].lower() or "admins" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_exclude_personnel_not_in_nominal_roll(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_nominal_roll, sample_users,
):
    """Cannot exclude a personnel that doesn't belong to the grouping's nominal_roll."""
    admin_id = str(sample_users["admin"].id)
    grouping = await _make_draft_grouping(db_session, str(sample_nominal_roll.id), admin_id)

    response = client.post(
        f"/api/v1/groupings/{grouping.id}/exclusions",
        json={"personnel_id": "nonexistent-personnel-id"},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "not found in this grouping" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_exclude_personnel_non_draft_grouping(
    client: TestClient, admin_token_headers: dict[str, str], sample_grouping,
    sample_personnel, sample_users,
):
    """Cannot exclude from a non-draft (active) grouping."""
    admin_id = str(sample_users["admin"].id)

    response = client.post(
        f"/api/v1/groupings/{sample_grouping.id}/exclusions",
        json={"personnel_id": str(sample_personnel[0].id)},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "draft" in response.json()["detail"].lower()


# ============================================================================
# DELETE /api/v1/groupings/{id}/exclusions/{personnel_id} — re-include
# ============================================================================


@pytest.mark.asyncio
async def test_include_personnel(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_nominal_roll, sample_personnel, sample_users,
):
    """Admin can re-include a previously excluded personnel."""
    admin_id = str(sample_users["admin"].id)
    grouping = await _make_draft_grouping(db_session, str(sample_nominal_roll.id), admin_id)
    personnel_id = str(sample_personnel[0].id)

    # Exclude first
    client.post(
        f"/api/v1/groupings/{grouping.id}/exclusions",
        json={"personnel_id": personnel_id},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    # Re-include
    response = client.delete(
        f"/api/v1/groupings/{grouping.id}/exclusions/{personnel_id}",
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 200
    assert "re-included" in response.json()["detail"].lower()

    # Verify personnel reappears in listing
    list_response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(grouping.id),
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    ids = [p["id"] for p in list_response.json()]
    assert personnel_id in ids


@pytest.mark.asyncio
async def test_include_personnel_not_excluded(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_nominal_roll, sample_personnel, sample_users,
):
    """404 when trying to re-include a personnel that isn't excluded."""
    admin_id = str(sample_users["admin"].id)
    grouping = await _make_draft_grouping(db_session, str(sample_nominal_roll.id), admin_id)

    response = client.delete(
        f"/api/v1/groupings/{grouping.id}/exclusions/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 404
    assert "not excluded" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_include_personnel_non_draft_grouping(
    client: TestClient, admin_token_headers: dict[str, str], sample_grouping,
    sample_personnel, sample_users,
):
    """Cannot re-include from a non-draft grouping."""
    admin_id = str(sample_users["admin"].id)

    response = client.delete(
        f"/api/v1/groupings/{sample_grouping.id}/exclusions/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    assert response.status_code == 400
    assert "draft" in response.json()["detail"].lower()


# ============================================================================
# Integration — listing respects exclusions
# ============================================================================


@pytest.mark.asyncio
async def test_excluded_personnel_not_in_listing(
    client: TestClient, admin_token_headers: dict[str, str], db_session: AsyncSession,
    sample_nominal_roll, sample_personnel, sample_users,
):
    """Comprehensive: exclude one person, verify listing count drops and person absent."""
    admin_id = str(sample_users["admin"].id)
    grouping = await _make_draft_grouping(db_session, str(sample_nominal_roll.id), admin_id)

    # Baseline: all 3 personnel listed
    baseline = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(grouping.id),
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    assert len(baseline.json()) == 3

    # Exclude one
    excluded_id = str(sample_personnel[1].id)
    client.post(
        f"/api/v1/groupings/{grouping.id}/exclusions",
        json={"personnel_id": excluded_id},
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "admin"},
    )

    # Verify: count drops by 1, excluded person absent
    after = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(grouping.id),
            "user_id": admin_id,
            "user_role": "admin",
        },
    )
    after_data = after.json()
    assert len(after_data) == 2
    assert all(p["id"] != excluded_id for p in after_data)
