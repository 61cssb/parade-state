"""Tests for personnel management API endpoints."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models.grouping import (
    Grouping,
    GroupingNotes,
    GroupingPersonnelOverride,
)
from parade_state.models.csv_ingestion import NominalRoll
from parade_state.models.personnel import Personnel
from tests.test_utils import (
    assert_404_response,
    assert_pagination_works,
    assert_permission_denied,
)


@pytest.mark.asyncio
async def test_list_personnel_with_grouping_context(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_grouping: Grouping,
    sample_personnel,
    sample_users,
):
    """Test listing personnel with grouping context."""
    admin_id = str(sample_users["admin"].id)

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": admin_id,
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check first personnel has grouping context
    first_personnel = data[0]
    assert "id" in first_personnel
    assert "name" in first_personnel
    assert "pers_no" in first_personnel
    assert "grouping_id" in first_personnel
    assert first_personnel["grouping_id"] == str(sample_grouping.id)
    assert "has_override" in first_personnel
    assert "grouping_notes" in first_personnel


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

    # Check personnel don't have grouping context
    first_personnel = data[0]
    assert "id" in first_personnel
    assert "name" in first_personnel
    assert first_personnel["grouping_id"] is None
    assert first_personnel["has_override"] is False
    assert first_personnel["grouping_notes"] is None


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
        expected_detail="Only admins can list personnel without grouping context",
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test listing personnel with unit filter."""
    # Get the unit of first personnel
    first_unit = sample_personnel[0].unit

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
                "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test listing personnel with search functionality."""
    # Search for first personnel's name
    search_term = sample_personnel[0].full_name[:5]  # Use first 5 characters

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test listing personnel with search by pers_no."""
    # Search for first personnel's pers_no (first 5 chars)
    search_term = sample_personnel[0].pers_no[:5]

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
async def test_list_personnel_with_overrides(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_grouping: Grouping,
    sample_personnel,
    sample_users,
):
    """Test listing personnel with grouping overrides."""
    # Create override for first personnel
    override = GroupingPersonnelOverride(
        grouping_id=str(sample_grouping.id),
        personnel_id=str(sample_personnel[0].id),
        unit="Override Unit",
        sub_unit_1="Override Subunit",
        created_by=str(sample_users["admin"].id),
    )

    db_session.add(override)
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Find the personnel with override
    personnel_with_override = None
    for personnel in data:
        if personnel["id"] == str(sample_personnel[0].id):
            personnel_with_override = personnel
            break

    assert personnel_with_override is not None
    assert personnel_with_override["has_override"] is True


@pytest.mark.asyncio
async def test_list_personnel_with_grouping_notes(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_grouping: Grouping,
    sample_personnel,
    sample_users,
):
    """Test listing personnel with grouping notes."""
    # Create grouping notes for first personnel
    notes = GroupingNotes(
        grouping_id=str(sample_grouping.id),
        personnel_id=str(sample_personnel[0].id),
        notes="Medical exemption granted",
        created_by=str(sample_users["admin"].id),
        updated_by=str(sample_users["admin"].id),
    )

    db_session.add(notes)
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Find the personnel with notes
    personnel_with_notes = None
    for personnel in data:
        if personnel["id"] == str(sample_personnel[0].id):
            personnel_with_notes = personnel
            break

    assert personnel_with_notes is not None
    assert personnel_with_notes["grouping_notes"] == "Medical exemption granted"


@pytest.mark.asyncio
async def test_get_personnel_by_id_with_grouping_context(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test getting personnel by ID with grouping context."""
    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["name"] == sample_personnel[0].full_name
    assert data["grouping_id"] == str(sample_grouping.id)
    assert "has_override" in data
    assert "grouping_notes" in data


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
    assert data["grouping_id"] is None
    assert data["has_override"] is False
    assert data["grouping_notes"] is None


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
        expected_detail="Only admins can view personnel without grouping context",
        params={
            "user_id": "user-id",
            "user_role": "user",
        },
    )


@pytest.mark.asyncio
async def test_get_personnel_by_id_with_override_and_notes(
    client: TestClient,
    admin_token_headers: dict[str, str],
    db_session,
    sample_grouping: Grouping,
    sample_personnel,
    sample_users,
):
    """Test getting personnel by ID with override and notes."""
    # Create override
    override = GroupingPersonnelOverride(
        grouping_id=str(sample_grouping.id),
        personnel_id=str(sample_personnel[0].id),
        unit="Override Unit",
        sub_unit_1="Override Subunit",
        created_by=str(sample_users["admin"].id),
    )

    db_session.add(override)

    # Create notes
    notes = GroupingNotes(
        grouping_id=str(sample_grouping.id),
        personnel_id=str(sample_personnel[0].id),
        notes="Medical exemption granted",
        created_by=str(sample_users["admin"].id),
        updated_by=str(sample_users["admin"].id),
    )

    db_session.add(notes)
    await db_session.commit()

    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_personnel[0].id)
    assert data["has_override"] is True
    assert data["grouping_notes"] == "Medical exemption granted"


@pytest.mark.asyncio
async def test_update_personnel_as_admin(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_grouping: Grouping,
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
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Rank/name edits are rejected with 409 — the NR is read-only."""
    for payload in ({"rank": "CPL"}, {"name": "New Name"}):
        response = client.patch(
            f"/api/v1/personnel/{sample_personnel[0].id}",
            headers=admin_token_headers,
            params={
                "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
            "grouping_id": str(sample_grouping.id),
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
            "grouping_id": str(sample_grouping.id),
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
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Under the 1:1 read-only NR model, rank changes are rejected (409)
    rather than recomputing category. Category follows the CSV source."""
    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json={"rank": "CPT"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_update_personnel_invalid_rank_rejected(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_grouping: Grouping,
    sample_personnel,
):
    """Rank edits are identity edits — rejected with 409 under the read-only
    NR model (no rank-validation 400 path is reachable via PATCH)."""
    response = client.patch(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
        json={"rank": "SGT"},
    )
    assert response.status_code == 409


async def test_list_personnel_with_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    sample_grouping: Grouping,
):
    """Test listing personnel with pagination."""
    assert_pagination_works(
        client,
        "/api/v1/personnel",
        admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )


async def test_list_personnel_invalid_grouping_id(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
):
    """Test listing personnel with invalid grouping ID."""
    assert_404_response(
        client,
        "get",
        "/api/v1/personnel",
        admin_token_headers,
        params={
            "grouping_id": "invalid-grouping-id",
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


@pytest.mark.asyncio
async def test_list_personnel_from_different_nominal_roll_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_grouping: Grouping,
    sample_nominal_roll,
):
    """Test that personnel from different nominal_roll are not returned."""
    # Create a real second nominal roll (Postgres enforces the FK) and
    # personnel belonging to it
    other_roll = NominalRoll(
        caa=date(2024, 2, 1),
        csv_hash="other_hash",
        personnel_count=1,
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(other_roll)
    await db_session.commit()

    other_personnel = Personnel(
        nominal_roll_id=str(other_roll.id),
        rank="Private",
        category="WOSE",
        full_name="Other Person",
        unit="Other Unit",
        created_by="admin-user-id",
    )

    db_session.add(other_personnel)
    await db_session.commit()

    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Other personnel should not be in the list
    other_found = False
    for personnel in data:
        if personnel["id"] == str(other_personnel.id):
            other_found = True
            break

    assert other_found is False


@pytest.mark.asyncio
async def test_get_personnel_from_different_nominal_roll_forbidden(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_grouping: Grouping,
    sample_nominal_roll,
):
    """Test that getting personnel from different nominal_roll returns error."""
    # Create a real second nominal roll (Postgres enforces the FK) and
    # personnel belonging to it
    other_roll = NominalRoll(
        caa=date(2024, 2, 1),
        csv_hash="other_hash",
        personnel_count=1,
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(other_roll)
    await db_session.commit()

    other_personnel = Personnel(
        nominal_roll_id=str(other_roll.id),
        rank="Private",
        category="WOSE",
        full_name="Other Person",
        unit="Other Unit",
        created_by="admin-user-id",
    )

    db_session.add(other_personnel)
    await db_session.commit()

    response = client.get(
        f"/api/v1/personnel/{other_personnel.id}",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
            "user_id": str(sample_users["admin"].id),
            "user_role": "admin",
        },
    )

    assert response.status_code == 400
    assert (
        "does not belong to this grouping's nominal roll"
        in response.json()["detail"]
    )


# ============================================================================
# Session 3 Tests: Audit Trail, Sorting, and Enhanced Validation
# ============================================================================


@pytest.mark.asyncio
async def test_update_personnel_sets_audit_trail(
    client: TestClient,
    admin_token_headers: dict[str, str],
    sample_users,
    db_session,
    sample_grouping: Grouping,
    sample_personnel,
):
    """A status update sets audit fields on the personnel row. (Under the
    1:1 model, identity edits are rejected — so we audit via status.)"""
    import time

    get_response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test sorting personnel by name ascending."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test sorting personnel by name descending."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test sorting personnel by rank."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test sorting personnel by status."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test that invalid sort field is ignored (doesn't cause error)."""
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
    sample_personnel,
):
    """Test that personnel responses include audit trail fields."""
    response = client.get(
        f"/api/v1/personnel/{sample_personnel[0].id}",
        headers=admin_token_headers,
        params={
            "grouping_id": str(sample_grouping.id),
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
    sample_grouping: Grouping,
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
            "grouping_id": str(sample_grouping.id),
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
