"""Tests for personnel management API endpoints."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.csv_ingestion import NominalRoll
from parade_state.models.personnel import Personnel
from parade_state.models.audit import AuditLog
from tests.test_utils import (
    assert_404_response,
    assert_pagination_works,
    assert_permission_denied,
)


@pytest.mark.asyncio
async def test_list_personnel_without_grouping_context_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_personnel,
    sample_users,
):
    """Test listing personnel without grouping context as admin."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    first_personnel = data[0]
    assert "id" in first_personnel
    assert "name" in first_personnel
    assert "grouping_id" not in first_personnel


@pytest.mark.asyncio
async def test_list_personnel_without_grouping_context_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
):
    """Test that regular users cannot list personnel without grouping context."""
    assert_permission_denied(
        client,
        "get",
        "/api/v1/personnel",
        user_token_headers,
        expected_detail="Only admins can list personnel",
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
    )


@pytest.mark.asyncio
async def test_list_personnel_with_unit_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test listing personnel with unit filter."""
    # Get the unit of first personnel
    first_unit = sample_personnel[0].unit

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "unit": first_unit,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)

    # All returned personnel should have the specified unit
    for personnel in data:
        assert personnel["unit"] == first_unit


@pytest.mark.asyncio
async def test_list_personnel_with_sub_unit_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test listing personnel with sub-unit filter."""
    # Get the sub_unit_1 of first personnel that has one
    sub_unit = None
    for p in sample_personnel:
        if p.sub_unit_1:
            sub_unit = p.sub_unit_1
            break

    if sub_unit:
        response = client.get(
            "/api/v1/personnel",
            headers=admin_token_headers,
            params={
                    "sub_unit_1": sub_unit,
                "user_id": str(sample_users["admin"].id),
                "user_role": "admin",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

        # All returned personnel should have the specified sub_unit_1
        for personnel in data:
            assert personnel["sub_unit_1"] == sub_unit


@pytest.mark.asyncio
async def test_list_personnel_with_search(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test listing personnel with search functionality."""
    # Search for first personnel's name
    search_term = sample_personnel[0].full_name[:5]  # Use first 5 characters

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "search": search_term,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check that search term matches
    found = False
    for personnel in data:
        if search_term.lower() in personnel["name"].lower():
            found = True
            break
    assert found, "Search term should match at least one personnel"


@pytest.mark.asyncio
async def test_list_personnel_with_search_by_pers_no(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test listing personnel with search by pers_no."""
    # Search for first personnel's pers_no (first 5 chars)
    search_term = sample_personnel[0].pers_no[:5]

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "search": search_term,
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check that search term matches pers_no
    found = False
    for personnel in data:
        if search_term.lower() in personnel["pers_no"].lower():
            found = True
            break
    assert found, "Search term should match at least one personnel pers_no"


@pytest.mark.asyncio
async def test_get_personnel_by_id_without_grouping_context_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """Test getting personnel by ID without grouping context as admin."""
    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["name"] == sample_personnel[0].full_name
    assert "grouping_id" not in data


async def test_get_personnel_by_id_without_grouping_context_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_personnel,
):
    """Test that regular users cannot get personnel without grouping context."""
    assert_permission_denied(
        client,
        "get",
        f"/api/v1/personnel/{sample_personnel[0].id}",
        user_token_headers,
        expected_detail="Only admins can view personnel",
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
    )


@pytest.mark.asyncio
async def test_update_personnel_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Unit/subunit edits are redirected to a TaggingEntry overlay; the
    personnel row stays read-only. Response returns effective values."""
    original_unit = sample_personnel[0].unit
    update_data = {
        "unit": "Remapped Unit",
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    # Effective unit reflects the remap; canonical personnel row unchanged.
    assert data["unit"] == "Remapped Unit"
    # The personnel row itself was not mutated.
    await db_session.refresh(sample_personnel[0])
    assert sample_personnel[0].unit == original_unit


@pytest.mark.asyncio
async def test_update_personnel_remap_upserts_tagging_entry(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Two sequential remaps on the same person produce ONE tagging entry
    whose ``to_*`` values reflect both edits (no duplicate rows)."""
    p = sample_personnel[0]
    base_params = {
        "user_id": str(sample_users["admin"].id),
        "user_role": "admin",
    }

    r1 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"sub_unit_1": "New S1"},
    )
    assert r1.status_code == 200
    assert r1.json()["sub_unit_1"] == "New S1"

    r2 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"sub_unit_2": "New S2"},
    )
    assert r2.status_code == 200
    body = r2.json()
    # Both edits preserved on the single entry.
    assert body["sub_unit_1"] == "New S1"
    assert body["sub_unit_2"] == "New S2"


@pytest.mark.asyncio
async def test_update_personnel_identity_fields_rejected(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """Rank/name edits are rejected with 409 — the NR is read-only."""
    for payload in ({"rank": "CPL"}, {"name": "New Name"}):
        response = client.patch(
            f"/api/v1/personnel/{sample_personnel[0].id}",
            headers=admin_token_headers,
            params={
                    "user_id": str(sample_users["admin"].id),
                "user_role": "admin",
            },
            json=payload,
        )
        assert response.status_code == 409, payload
        assert "read-only" in response.json()["detail"].lower()


async def test_update_personnel_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_personnel,
):
    """Test that regular users cannot update personnel."""
    update_data = {
        "rank": "Updated Rank",
    }

    assert_permission_denied(
        client,
        "patch",
        f"/api/v1/personnel/{sample_personnel[0].id}",
        user_token_headers,
        expected_detail="Only admins can update personnel records",
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
        json_data=update_data,
    )


@pytest.mark.asyncio
async def test_update_personnel_status(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test updating personnel status."""
    update_data = {
        "status": "archived",
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["status"] == "archived"


@pytest.mark.asyncio
async def test_list_personnel_with_status_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test listing personnel with status filter."""
    # Archive first personnel
    sample_personnel[0].status = "archived"
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "status": "archived",
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # All returned personnel should have archived status
    for personnel in data:
        assert personnel["status"] == "archived"


@pytest.mark.asyncio
async def test_list_personnel_with_category_filter(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """Test listing personnel filtered by category (Officer / WOSE).

    The shared fixture has one Officer (CPT) and two WOSEs (PTE, CPL).
    """
    # Officer filter -> only the CPT
    officer_resp = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "category": "Officer",
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert officer_resp.status_code == 200
    officer_data = officer_resp.json()
    assert len(officer_data) >= 1
    for personnel in officer_data:
        assert personnel["category"] == "Officer"

    # WOSE filter -> only PTE and CPL
    wose_resp = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "category": "WOSE",
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert wose_resp.status_code == 200
    wose_data = wose_resp.json()
    assert len(wose_data) >= 1
    for personnel in wose_data:
        assert personnel["category"] == "WOSE"

    # No filter returns both categories
    all_resp = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )
    assert all_resp.status_code == 200
    categories = {p["category"] for p in all_resp.json()}
    assert categories == {"Officer", "WOSE"}


@pytest.mark.asyncio
async def test_update_personnel_status_only_does_not_touch_tagging(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Updating only ``status`` mutates the personnel row directly and does
    not create a tagging entry (status is not a remap field)."""
    from sqlalchemy import select

    from parade_state.models import TaggingEntry

    p = sample_personnel[0]
    response = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json={"status": "archived"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    await db_session.refresh(p)
    assert p.status == "archived"
    # No tagging entry was created.
    rows = (await db_session.execute(
        select(TaggingEntry).where(TaggingEntry.personnel_id == p.id)
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_update_personnel_recomputes_category(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Under the 1:1 read-only NR model, rank changes are rejected (409)
    rather than recomputing category. Category follows the CSV source."""
    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json={"rank": "CPT"},
    )
    assert response.status_code == 409


async def test_list_personnel_with_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Test listing personnel with pagination."""
    assert_pagination_works(
        client,
        "/api/v1/personnel",
        admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )


async def test_get_personnel_invalid_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Test getting personnel with invalid ID."""
    assert_404_response(
        client,
        "get",
        "/api/v1/personnel/invalid-personnel-id",
        admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )


async def test_update_personnel_invalid_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Updating an unknown personnel id returns 404 (uses a remap field so
    the identity-field 409 path doesn't short-circuit first)."""
    response = client.patch(
        "/api/v1/personnel/invalid-personnel-id",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json={"unit": "Some Unit"},
    )

    assert response.status_code == 404


# ============================================================================
# Session 3 Tests: Audit Trail, Sorting, and Enhanced Validation
# ============================================================================


@pytest.mark.asyncio
async def test_update_personnel_sets_audit_trail(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """A status update sets audit fields on the personnel row. (Under the
    1:1 model, identity edits are rejected — so we audit via status.)"""
    import time

    get_response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert get_response.status_code == 200
    original_data = get_response.json()
    original_updated_at = original_data.get("updated_at")

    time.sleep(0.1)

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json={"status": "archived"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["status"] == "archived"
    assert data["updated_at"] is not None
    assert data["updated_by"] == str(sample_users["admin"].id)

    if original_updated_at:
        assert data["updated_at"] != original_updated_at


@pytest.mark.asyncio
async def test_list_personnel_sort_by_name_asc(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test sorting personnel by name ascending."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "name",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify names are in ascending order
    names = [p["name"] for p in data]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_list_personnel_sort_by_name_desc(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test sorting personnel by name descending."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "name",
            "sort_order": "desc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify names are in descending order
    names = [p["name"] for p in data]
    assert names == sorted(names, reverse=True)


@pytest.mark.asyncio
async def test_list_personnel_sort_by_rank(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test sorting personnel by rank."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "rank",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify ranks are sorted
    ranks = [p["rank"] for p in data]
    assert ranks == sorted(ranks)


@pytest.mark.asyncio
async def test_list_personnel_sort_by_status(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test sorting personnel by status."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "status",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify statuses are sorted
    statuses = [p["status"] for p in data]
    assert statuses == sorted(statuses)


@pytest.mark.asyncio
async def test_list_personnel_invalid_sort_field_ignored(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test that invalid sort field is ignored (doesn't cause error)."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "sort_by": "invalid_field",  # Invalid field
            "sort_order": "asc",
        },
    )

    # Should not error, just ignore invalid sort field
    assert response.status_code == 200
    assert len(response.json()) > 0


@pytest.mark.asyncio
async def test_update_personnel_invalid_status(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test that updating personnel with invalid status fails validation."""
    update_data = {
        "status": "invalid_status",  # Not 'active' or 'archived'
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    # Should fail validation
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_personnel_empty_rank(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test that updating personnel with empty rank fails validation."""
    update_data = {
        "rank": "",  # Empty string
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    # Should fail validation
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_personnel_too_long_name(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test that updating personnel with too long name fails validation."""
    update_data = {
        "name": "A" * 256,  # Exceeds max_length of 255
    }

    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json=update_data,
    )

    # Should fail validation
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_personnel_response_includes_audit_fields(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test that personnel responses include audit trail fields."""
    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Verify audit fields are present
    assert "created_at" in data
    assert "updated_at" in data
    assert "created_by" in data
    assert "updated_by" in data


@pytest.mark.asyncio
async def test_list_personnel_with_filters_and_sorting(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Test combining filters with sorting."""
    # Archive some personnel
    sample_personnel[0].status = "archived"
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
            "status": "archived",
            "sort_by": "name",
            "sort_order": "asc",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Should only return archived personnel
    assert all(p["status"] == "archived" for p in data)

    # Should be sorted by name
    names = [p["name"] for p in data]
    assert names == sorted(names)


# ============================================================================
# Callup status & remarks (issue 06 — NR status & remarks columns)
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callup_status",
    ["Called Up", "Deferred", "Disrupted", "MR", "Age Limit", "Other"],
)
async def test_update_personnel_callup_status_all_values(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
    callup_status: str,
):
    """Admins can set any of the six callup statuses; the row and response
    reflect the change immediately."""
    p = sample_personnel[0]

    response = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json={"callup_status": callup_status},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["callup_status"] == callup_status

    await db_session.refresh(p)
    assert p.callup_status == callup_status


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", ["Not Called Up", "deferred", "called", ""])
async def test_update_personnel_callup_status_invalid_rejected(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
    bad_value: str,
):
    """Values outside the six-status enum are rejected with 422. The check is
    case-sensitive: 'deferred' is not 'Deferred'."""
    p = sample_personnel[0]

    response = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json={"callup_status": bad_value},
    )

    assert response.status_code == 422, bad_value


@pytest.mark.asyncio
async def test_update_personnel_remarks_set_and_clear(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Remarks are settable, whitespace-normalised, and clearable (empty
    string or explicit null both clear)."""
    p = sample_personnel[0]
    base_params = {
        "user_id": str(sample_users["admin"].id),
        "user_role": "admin",
    }

    r1 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"remarks": "  On course until Friday  "},
    )
    assert r1.status_code == 200
    assert r1.json()["remarks"] == "On course until Friday"

    r2 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"remarks": ""},
    )
    assert r2.status_code == 200
    await db_session.refresh(p)
    assert p.remarks is None

    r3 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"remarks": "temp"},
    )
    assert r3.status_code == 200
    r4 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"remarks": None},
    )
    assert r4.status_code == 200
    await db_session.refresh(p)
    assert p.remarks is None


@pytest.mark.asyncio
async def test_update_personnel_callup_fields_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_personnel,
):
    """Non-admins cannot change callup_status or remarks."""
    p = sample_personnel[0]

    for payload in ({"callup_status": "Deferred"}, {"remarks": "nope"}):
        response = client.patch(
            f"/api/v1/personnel/{p.id}",
            headers=user_token_headers,
            params={"user_id": "user-id", "user_role": "user"},
            json=payload,
        )
        assert response.status_code == 403, payload


# ============================================================================
# Manual serviceman creation (issue 26)
# ============================================================================


def _create_payload(nominal_roll_id: str, **overrides) -> dict:
    """Minimal valid PersonnelCreate body for the given roll."""
    payload = {
        "nominal_roll_id": str(nominal_roll_id),
        "rank": "PTE",
        "name": "Manual Person",
        "unit": "Coy A",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_personnel_manual_without_pers_no(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_nominal_roll,
    sample_personnel,
    db_session,
):
    """Super-admin can add a serviceman with unknown pers_no. The row carries
    source="manual", the defaults make it manageable like any other row, the
    roll's personnel_count increments, and the create is audited."""
    admin_id = str(sample_users["admin"].id)

    response = client.post(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "super_admin"},
        json=_create_payload(sample_nominal_roll.id),
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["pers_no"] is None
    assert data["source"] == "manual"
    assert data["status"] == "active"
    assert data["callup_status"] == "Called Up"
    assert data["category"] == "WOSE"  # inferred from PTE
    assert data["created_by"] == admin_id

    await db_session.refresh(sample_nominal_roll)
    assert sample_nominal_roll.personnel_count == 4  # 3 sample rows + 1

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "personnel",
                AuditLog.entity_id == data["id"],
                AuditLog.action == "create",
            )
        )
    ).scalar_one()
    assert "Manually added" in audit.description
    assert audit.user_id == admin_id

    # Multiple unknown-pers_no rows per roll are legal (NULLs are distinct).
    second = client.post(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={"user_id": admin_id, "user_role": "super_admin"},
        json=_create_payload(sample_nominal_roll.id, name="Second Manual"),
    )
    assert second.status_code == 201


@pytest.mark.asyncio
async def test_create_personnel_manual_with_pers_no_and_fields(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_nominal_roll,
    db_session,
):
    """Full-field manual create: pers_no, sub-units, callup override and
    remarks (whitespace-normalised) round-trip."""
    response = client.post(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "super_admin",
        },
        json=_create_payload(
            sample_nominal_roll.id,
            rank="LTA",
            name="Manual Officer",
            pers_no="  88880001  ",
            unit="Coy B",
            sub_unit_1="Platoon 3",
            sub_unit_2="  ",
            callup_status="Deferred",
            remarks="  On course  ",
        ),
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["pers_no"] == "88880001"  # stripped
    assert data["rank"] == "LTA"
    assert data["category"] == "Officer"
    assert data["unit"] == "Coy B"
    assert data["sub_unit_1"] == "Platoon 3"
    assert data["sub_unit_2"] is None  # blank becomes NULL
    assert data["callup_status"] == "Deferred"
    assert data["remarks"] == "On course"

    row = (
        await db_session.execute(
            select(Personnel).where(Personnel.id == data["id"])
        )
    ).scalar_one()
    assert row.source == "manual"


@pytest.mark.asyncio
async def test_create_personnel_permission_gates(
    client: TestClient,
    admin_token_headers: dict[str, str],
    user_token_headers: dict[str, str],
    sample_users,
    sample_nominal_roll,
):
    """Manual creation is super-admin only: admins and users get 403."""
    for headers, role in (
        (admin_token_headers, "admin"),
        (user_token_headers, "user"),
    ):
        response = client.post(
            "/api/v1/personnel",
            headers=headers,
            params={
                "user_id": str(sample_users["admin"].id),
                "user_role": role,
            },
            json=_create_payload(sample_nominal_roll.id),
        )
        assert response.status_code == 403, role
        assert "super-admin" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_personnel_unknown_nominal_roll(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """An unknown nominal_roll_id is a 404, not a 500 (FK guard)."""
    response = client.post(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "super_admin",
        },
        json=_create_payload("00000000-0000-0000-0000-000000000000"),
    )
    assert response.status_code == 404
    assert "Nominal roll not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_personnel_invalid_rank(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_nominal_roll,
):
    """Unknown ranks are rejected with 400 and the valid rank list."""
    response = client.post(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "super_admin",
        },
        json=_create_payload(sample_nominal_roll.id, rank="SGT"),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Invalid rank" in detail
    assert "CPL" in detail and "LTA" in detail  # valid ranks are listed


@pytest.mark.asyncio
async def test_create_personnel_duplicate_pers_no_within_roll(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_nominal_roll,
    sample_personnel,
    db_session,
):
    """Duplicate pers_no on the same roll is a 409; the same pers_no on a
    different roll stays allowed (matches CSV semantics)."""
    admin_id = str(sample_users["admin"].id)
    super_params = {"user_id": admin_id, "user_role": "super_admin"}

    dup = client.post(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params=super_params,
        json=_create_payload(
            sample_nominal_roll.id, pers_no="10000001"  # sample_personnel[0]
        ),
    )
    assert dup.status_code == 409
    assert "10000001" in dup.json()["detail"]

    other_roll = NominalRoll(
        caa=date(2024, 2, 1),
        csv_hash="hash-26",
        personnel_count=0,
        uploaded_by=admin_id,
    )
    db_session.add(other_roll)
    await db_session.commit()

    ok = client.post(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params=super_params,
        json=_create_payload(other_roll.id, pers_no="10000001"),
    )
    assert ok.status_code == 201


# ============================================================================
# PATCH pers_no: super-admin fill-in-later flow (issue 26)
# ============================================================================


@pytest.mark.asyncio
async def test_update_personnel_pers_no_set_and_clear(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_personnel,
):
    """Super-admin can fill in pers_no later and clear it again (empty or
    explicit null); updates stamp updated_at/updated_by."""
    p = sample_personnel[0]
    base_params = {
        "user_id": str(sample_users["admin"].id),
        "user_role": "super_admin",
    }

    r1 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"pers_no": " 77770001 "},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["pers_no"] == "77770001"  # stripped
    await db_session.refresh(p)
    assert p.pers_no == "77770001"
    assert p.updated_by == str(sample_users["admin"].id)
    assert p.updated_at is not None

    r2 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"pers_no": ""},
    )
    assert r2.status_code == 200
    await db_session.refresh(p)
    assert p.pers_no is None

    r3 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"pers_no": "77770002"},
    )
    assert r3.status_code == 200
    r4 = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=base_params,
        json={"pers_no": None},
    )
    assert r4.status_code == 200
    await db_session.refresh(p)
    assert p.pers_no is None


@pytest.mark.asyncio
async def test_update_personnel_pers_no_duplicate_rejected(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
    db_session,
):
    """Setting a pers_no already used on the same roll (by another person)
    is a 409 with a clear message."""
    p = sample_personnel[0]  # 10000001
    response = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params={
            "user_id": str(sample_users["admin"].id),
            "user_role": "super_admin",
        },
        json={"pers_no": "10000002"},  # sample_personnel[1]
    )
    assert response.status_code == 409
    assert "10000002" in response.json()["detail"]
    await db_session.refresh(p)
    assert p.pers_no == "10000001"  # unchanged


@pytest.mark.asyncio
async def test_update_personnel_pers_no_as_admin_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """Admins cannot change pers_no (403) but keep the other PATCH fields."""
    p = sample_personnel[0]
    params = {"user_id": str(sample_users["admin"].id), "user_role": "admin"}

    blocked = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=params,
        json={"pers_no": "12345678"},
    )
    assert blocked.status_code == 403
    assert "personnel numbers" in blocked.json()["detail"].lower()

    allowed = client.patch(
        f"/api/v1/personnel/{p.id}",
        headers=admin_token_headers,
        params=params,
        json={"remarks": "admins keep this"},
    )
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_update_personnel_pers_no_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_personnel,
):
    """Regular users hit the admin gate for pers_no like every PATCH."""
    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=user_token_headers,
        params={"user_id": "user-id", "user_role": "user"},
        json={"pers_no": "12345678"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_personnel_response_includes_source(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_personnel,
):
    """The source provenance field is part of every personnel response."""
    params = {
        "user_id": str(sample_users["admin"].id),
        "user_role": "admin",
    }

    listing = client.get("/api/v1/personnel", headers=admin_token_headers, params=params)
    assert listing.status_code == 200
    assert all("source" in row for row in listing.json())
    assert all(row["source"] is None for row in listing.json())  # CSV rows

    single = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params=params,
    )
    assert single.status_code == 200
    assert single.json()["source"] is None
