"""Behavioral tests for admin scoped access (issue #28).

Covers the (unit, sub_unit_1) grant vocabulary with '*' wildcards,
deny-by-default on every scoped read/write surface, overlay-aware scope
(tagging remaps moving personnel in/out of scope), cosmetic-filter
non-bypass, pagination correctness, and the super-admin grant CRUD with
roster validation.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import Personnel, Tagging, TaggingEntry, UserSubunitAssignment

SUPER_ADMIN = {"user_id": "super-admin-test-id", "user_role": "super_admin"}
GRANT_SA = {"granted_by": "super-admin-test-id", "user_role": "super_admin"}


async def _grant(
    db_session: AsyncSession,
    nr_id,
    user_id: str,
    unit: str,
    sub_unit_1: str,
) -> None:
    """Insert a scope grant directly (bypasses API roster validation)."""
    db_session.add(
        UserSubunitAssignment(
            user_id=user_id,
            nominal_roll_id=str(nr_id),
            unit=unit,
            sub_unit_1=sub_unit_1,
            created_by="super-admin-test-id",
        )
    )
    await db_session.commit()


async def _extra_personnel(db_session: AsyncSession, nr_id) -> list[Personnel]:
    """A second unit and edge-case rows: Coy B / Platoon 1, Coy B / no
    sub-unit, and Coy A with no sub-unit."""
    rows = [
        Personnel(
            nominal_roll_id=str(nr_id),
            pers_no="10000004",
            rank="PTE",
            category="WOSE",
            full_name="Carol Nash",
            unit="Coy B",
            sub_unit_1="Platoon 1",
            created_by="super-admin-test-id",
        ),
        Personnel(
            nominal_roll_id=str(nr_id),
            pers_no="10000005",
            rank="LCP",
            category="WOSE",
            full_name="Dan Reed",
            unit="Coy B",
            sub_unit_1=None,
            created_by="super-admin-test-id",
        ),
        Personnel(
            nominal_roll_id=str(nr_id),
            pers_no="10000006",
            rank="ME3",
            category="WOSE",
            full_name="Eve Fox",
            unit="Coy A",
            sub_unit_1=None,
            created_by="super-admin-test-id",
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    return rows


def _names(response_json) -> set[str]:
    return {p["name"] for p in response_json}


# ============================================================================
# Grant vocabulary — who is visible in the personnel list
# ============================================================================


@pytest.mark.asyncio
async def test_unit_wildcard_sub_grant_covers_whole_unit(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """(unit='Coy A', sub_unit_1='*') sees every Coy A row — including
    Coy A personnel with a NULL sub_unit_1 — but nothing from Coy B."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    await _extra_personnel(db_session, sample_nominal_roll.id)
    await _grant(db_session, sample_nominal_roll.id, admin_id, "Coy A", "*")

    response = client.get(
        "/api/v1/personnel",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "limit": 100,
        },
    )
    assert response.status_code == 200
    assert _names(response.json()) == {"John Doe", "Jane Smith", "Bob Johnson", "Eve Fox"}


@pytest.mark.asyncio
async def test_composite_grant_sees_only_that_pair(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """(unit='Coy A', sub_unit_1='Platoon 1') sees exactly Coy A / Platoon 1 —
    not Coy B / Platoon 1, not NULL-sub-unit rows."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    await _extra_personnel(db_session, sample_nominal_roll.id)
    await _grant(db_session, sample_nominal_roll.id, admin_id, "Coy A", "Platoon 1")

    response = client.get(
        "/api/v1/personnel",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "limit": 100,
        },
    )
    assert response.status_code == 200
    assert _names(response.json()) == {"John Doe", "Jane Smith"}


@pytest.mark.asyncio
async def test_subunit_only_grant_spans_units(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """(unit='*', sub_unit_1='Platoon 1') — the pre-#28 shape — sees
    Platoon 1 under every unit."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    await _extra_personnel(db_session, sample_nominal_roll.id)
    await _grant(db_session, sample_nominal_roll.id, admin_id, "*", "Platoon 1")

    response = client.get(
        "/api/v1/personnel",
        params={
            "nominal_roll_id": str(sample_nominal_roll.id),
            "limit": 100,
        },
    )
    assert response.status_code == 200
    assert _names(response.json()) == {"John Doe", "Jane Smith", "Carol Nash"}


@pytest.mark.asyncio
async def test_null_sub_unit_only_matched_by_wildcard_grants(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """A NULL effective sub_unit_1 is invisible to concrete-subunit grants
    (closes the pre-#28 hole) and visible under sub-unit wildcards."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    extra = await _extra_personnel(db_session, sample_nominal_roll.id)
    dan = extra[1]  # Coy B, no sub-unit

    await _grant(db_session, sample_nominal_roll.id, admin_id, "Coy B", "Platoon 1")
    response = client.get(
        f"/api/v1/personnel/{dan.id}",
    )
    assert response.status_code == 403
    assert "Coy B/(no sub-unit)" in response.json()["detail"]

    await _grant(db_session, sample_nominal_roll.id, admin_id, "Coy B", "*")
    response = client.get(
        f"/api/v1/personnel/{dan.id}",
    )
    assert response.status_code == 200


# ============================================================================
# Deny-by-default on every scoped surface
# ============================================================================


@pytest.mark.asyncio
async def test_no_grants_denies_all_scoped_surfaces(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users, sample_attendance_scope, sample_attendance,
):
    """An admin with zero grants gets 403s on scoped NR surfaces and an
    empty cross-NR list — never other people's rows."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()
    pid = str(sample_personnel[0].id)

    assert client.get(
        "/api/v1/personnel",
        params={"nominal_roll_id": nr_id},
    ).status_code == 403

    assert client.get(
        "/api/v1/personnel",
    ).json() == []

    assert client.get(
        f"/api/v1/personnel/{pid}",
    ).status_code == 403

    assert client.get(
        f"/api/v1/personnel/{pid}/attendance-history",
    ).status_code == 403

    assert client.patch(
        f"/api/v1/personnel/{pid}",
        json={"remarks": "should not apply"},
    ).status_code == 403

    assert client.get(
        "/api/v1/attendance/",
        params={
            "nominal_roll_id": nr_id, "date": today,
        },
    ).status_code == 403

    assert client.get(
        f"/api/v1/nominal-rolls/{nr_id}",
    ).status_code == 403

    assert client.patch(
        f"/api/v1/nominal-rolls/{nr_id}",
        json={"notes": "nope"},
    ).status_code == 403

    assert client.get(
        f"/api/v1/nominal-rolls/{nr_id}/export",
    ).status_code == 403

    listed = client.get(
        "/api/v1/nominal-rolls",
    )
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.asyncio
async def test_super_admin_bypasses_all_scoped_surfaces(
    client: TestClient, client_as, db_session, sample_nominal_roll,
    sample_personnel, sample_attendance_scope,
):
    """Super-admin needs no grants anywhere."""
    client = await client_as("super_admin")
    nr_id = str(sample_nominal_roll.id)
    today = date.today().isoformat()
    pid = str(sample_personnel[0].id)

    assert client.get(
        "/api/v1/personnel",
        params={"nominal_roll_id": nr_id},
    ).status_code == 200

    assert client.get(
        f"/api/v1/personnel/{pid}",
    ).status_code == 200

    assert client.get(
        f"/api/v1/personnel/{pid}/attendance-history",
    ).status_code == 200

    assert client.get(
        "/api/v1/attendance/",
        params={"nominal_roll_id": nr_id, "date": today},
    ).status_code == 200

    assert client.get(
        f"/api/v1/nominal-rolls/{nr_id}",
    ).status_code == 200

    assert client.get(
        f"/api/v1/nominal-rolls/{nr_id}/export",
    ).status_code == 200


# ============================================================================
# Overlay-aware scope (tagging remaps)
# ============================================================================


@pytest.mark.asyncio
async def test_tagging_remap_moves_person_out_of_scope(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """A Platoon 1 admin loses a person the tagging remaps to Platoon 2 —
    absent from the list, 403 on detail and PATCH."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    nr_id = str(sample_nominal_roll.id)
    john = sample_personnel[0]  # Coy A / Platoon 1

    tagging = Tagging(
        label="remap-john-out",
        nominal_roll_id=nr_id,
        created_by="super-admin-test-id",
    )
    tagging.entries.append(
        TaggingEntry(
            personnel_id=str(john.id),
            from_unit="Coy A",
            from_sub_unit_1="Platoon 1",
            to_unit="Coy A",
            to_sub_unit_1="Platoon 2",
        )
    )
    db_session.add(tagging)
    await db_session.commit()

    await _grant(db_session, nr_id, admin_id, "Coy A", "Platoon 1")

    listed = client.get(
        "/api/v1/personnel",
        params={"nominal_roll_id": nr_id, "limit": 100},
    )
    assert listed.status_code == 200
    assert "John Doe" not in _names(listed.json())

    assert client.get(
        f"/api/v1/personnel/{john.id}",
    ).status_code == 403

    assert client.patch(
        f"/api/v1/personnel/{john.id}",
        json={"remarks": "blocked"},
    ).status_code == 403


@pytest.mark.asyncio
async def test_tagging_remap_moves_person_into_scope(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """A Platoon 1 admin gains the person the tagging remaps from
    Platoon 2 into Platoon 1 — the overlay, not the canonical row,
    decides."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    nr_id = str(sample_nominal_roll.id)
    bob = sample_personnel[2]  # Coy A / Platoon 2 canonically

    tagging = Tagging(
        label="remap-bob-in",
        nominal_roll_id=nr_id,
        created_by="super-admin-test-id",
    )
    tagging.entries.append(
        TaggingEntry(
            personnel_id=str(bob.id),
            from_unit="Coy A",
            from_sub_unit_1="Platoon 2",
            to_unit="Coy A",
            to_sub_unit_1="Platoon 1",
        )
    )
    db_session.add(tagging)
    await db_session.commit()

    await _grant(db_session, nr_id, admin_id, "Coy A", "Platoon 1")

    listed = client.get(
        "/api/v1/personnel",
        params={"nominal_roll_id": nr_id, "limit": 100},
    )
    assert listed.status_code == 200
    assert "Bob Johnson" in _names(listed.json())

    assert client.get(
        f"/api/v1/personnel/{bob.id}",
    ).status_code == 200


@pytest.mark.asyncio
async def test_pagination_over_scoped_rows_under_overlay(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """offset/limit pages over the in-scope subset only — every scoped row
    appears exactly once across pages, out-of-scope rows never."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    nr_id = str(sample_nominal_roll.id)
    await _extra_personnel(db_session, sample_nominal_roll.id)

    # Overlay moves John out of the admin's (Coy A, *) scope via a unit
    # remap, so the overlay path (not SQL) decides visibility.
    tagging = Tagging(
        label="remap-for-pagination",
        nominal_roll_id=nr_id,
        created_by="super-admin-test-id",
    )
    tagging.entries.append(
        TaggingEntry(
            personnel_id=str(sample_personnel[0].id),
            from_unit="Coy A",
            from_sub_unit_1="Platoon 1",
            to_unit="Coy B",
            to_sub_unit_1="Platoon 1",
        )
    )
    db_session.add(tagging)
    await db_session.commit()
    await _grant(db_session, nr_id, admin_id, "Coy A", "*")

    seen: list[str] = []
    for offset in (0, 1, 2):
        page = client.get(
            "/api/v1/personnel",
            params={
                "nominal_roll_id": nr_id, "limit": 1, "offset": offset,
            },
        )
        assert page.status_code == 200
        seen.extend(p["name"] for p in page.json())

    # Coy A rows minus John (remapped to Coy B): Jane, Bob, Eve.
    assert sorted(seen) == ["Bob Johnson", "Eve Fox", "Jane Smith"]


# ============================================================================
# Cosmetic filters cannot widen scope
# ============================================================================


@pytest.mark.asyncio
async def test_client_filters_cannot_widen_scope(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """Asking for a different unit/sub-unit than granted yields empty
    results — never foreign rows; granted filters still narrow."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    nr_id = str(sample_nominal_roll.id)
    await _extra_personnel(db_session, sample_nominal_roll.id)
    await _grant(db_session, nr_id, admin_id, "Coy A", "Platoon 1")

    base = {"nominal_roll_id": nr_id, "limit": 100}

    other_unit = client.get("/api/v1/personnel", params={**base, "unit": "Coy B"})
    assert other_unit.status_code == 200
    assert other_unit.json() == []

    other_sub = client.get("/api/v1/personnel", params={**base, "sub_unit_1": "Platoon 2"})
    assert other_sub.status_code == 200
    assert other_sub.json() == []

    narrower = client.get(
        "/api/v1/personnel",
        params={**base, "unit": "Coy A", "sub_unit_1": "Platoon 1"},
    )
    assert narrower.status_code == 200
    assert _names(narrower.json()) == {"John Doe", "Jane Smith"}


# ============================================================================
# Write boundary naming the missing assignment
# ============================================================================


@pytest.mark.asyncio
async def test_patch_out_of_scope_403_names_location(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """PATCH outside the grant fails 403 with the attendance-style message
    naming the unit/sub-unit pair; in-scope PATCH succeeds."""
    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    nr_id = str(sample_nominal_roll.id)
    await _grant(db_session, nr_id, admin_id, "Coy A", "Platoon 1")

    bob = sample_personnel[2]  # Coy A / Platoon 2
    denied = client.patch(
        f"/api/v1/personnel/{bob.id}",
        json={"remarks": "nope"},
    )
    assert denied.status_code == 403
    assert "No assignment for: Coy A/Platoon 2" in denied.json()["detail"]
    assert "Ask a super-admin" in denied.json()["detail"]

    john = sample_personnel[0]  # Coy A / Platoon 1
    allowed = client.patch(
        f"/api/v1/personnel/{john.id}",
        json={"remarks": "fine"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["remarks"] == "fine"


# ============================================================================
# NR visibility
# ============================================================================


@pytest.mark.asyncio
async def test_nr_list_shows_only_granted_rolls(
    client: TestClient, client_as, db_session, sample_nominal_roll, sample_users,
):
    """The NR list for a regular admin contains exactly the NRs they hold
    grants on; a grant-less roll is absent."""
    from parade_state.models import NominalRoll

    other_nr = NominalRoll(
        caa=date(2030, 1, 1),
        csv_hash="scope-list-other",
        uploaded_by="super-admin-test-id",
    )
    db_session.add(other_nr)
    await db_session.commit()

    admin_id = str(sample_users["admin"].id)
    client = await client_as(sample_users["admin"])
    listed = client.get(
        "/api/v1/nominal-rolls",
    )
    assert listed.status_code == 200
    assert listed.json() == []

    await _grant(db_session, sample_nominal_roll.id, admin_id, "Coy A", "*")
    listed = client.get(
        "/api/v1/nominal-rolls",
    )
    ids = {item["id"] for item in listed.json()}
    assert ids == {str(sample_nominal_roll.id)}


# ============================================================================
# Grant CRUD: validation, permissions, check constraint
# ============================================================================


@pytest.mark.asyncio
async def test_grant_rejects_values_absent_from_roster(
    client: TestClient, sample_nominal_roll, sample_personnel, sample_users,
):
    """Concrete grant values must exist on the roster (case-sensitive)."""
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)
    url = f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments"

    unknown_unit = client.post(url, params=GRANT_SA, json={"unit": "Coy Z", "sub_unit_1": "*"})
    assert unknown_unit.status_code == 400
    assert "Coy Z" in unknown_unit.json()["detail"]

    unknown_sub = client.post(url, params=GRANT_SA, json={"unit": "Coy A", "sub_unit_1": "Platoon 9"})
    assert unknown_sub.status_code == 400
    assert "Platoon 9" in unknown_sub.json()["detail"]

    valid_pairing = client.post(url, params=GRANT_SA, json={"unit": "Coy A", "sub_unit_1": "Platoon 2"})
    assert valid_pairing.status_code == 201  # Platoon 2 exists under Coy A

    # But a sub-unit that exists on the roster only under another unit is
    # rejected for this unit's grant.
    only_coy_a = client.post(url, params=GRANT_SA, json={"unit": "Coy B", "sub_unit_1": "Platoon 2"})
    assert only_coy_a.status_code == 400


@pytest.mark.asyncio
async def test_grant_rejects_empty_and_double_wildcard(
    client: TestClient, sample_nominal_roll, sample_personnel, sample_users,
):
    """'' never means wildcard, and (*, *) is refused outright."""
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)
    url = f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments"

    empty_unit = client.post(url, params=GRANT_SA, json={"unit": "", "sub_unit_1": "Platoon 1"})
    assert empty_unit.status_code == 422  # min_length=1 at the schema edge

    both_wildcard = client.post(url, params=GRANT_SA, json={"unit": "*", "sub_unit_1": "*"})
    assert both_wildcard.status_code == 400
    assert "specific units" in both_wildcard.json()["detail"]


@pytest.mark.asyncio
async def test_double_wildcard_blocked_by_check_constraint(
    db_session: AsyncSession, sample_nominal_roll, sample_users,
):
    """The DB-level CHECK constraint is the last line of defense."""
    import sqlalchemy as sa

    db_session.add(
        UserSubunitAssignment(
            user_id=str(sample_users["user"].id),
            nominal_roll_id=str(sample_nominal_roll.id),
            unit="*",
            sub_unit_1="*",
            created_by="super-admin-test-id",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_grant_non_super_admin_forbidden(
    client: TestClient, sample_nominal_roll, sample_personnel, sample_users,
):
    """Regular admins cannot manage grants."""
    user_id = str(sample_users["user"].id)
    response = client.post(
        f"/api/v1/access-control/nominal-rolls/{sample_nominal_roll.id}"
        f"/users/{user_id}/subunit-assignments",
        params={"granted_by": str(sample_users["admin"].id), "user_role": "admin"},
        json={"unit": "Coy A", "sub_unit_1": "Platoon 1"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_scope_options_list_roster_values(
    client: TestClient, db_session, sample_nominal_roll, sample_personnel,
):
    """scope-options feeds the grant form: units present on the roster and
    each unit's sub-unit values; NULL sub-units contribute nothing."""
    await _extra_personnel(db_session, sample_nominal_roll.id)
    response = client.get(
        f"/api/v1/access-control/nominal-rolls/{sample_nominal_roll.id}/scope-options",
        params=SUPER_ADMIN,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["units"] == ["Coy A", "Coy B"]
    assert data["subunits_by_unit"]["Coy A"] == ["Platoon 1", "Platoon 2"]
    assert data["subunits_by_unit"]["Coy B"] == ["Platoon 1"]


@pytest.mark.asyncio
async def test_grant_and_revoke_unit_scoped_grant(
    client: TestClient, sample_nominal_roll, sample_personnel, sample_users,
):
    """Grant (Coy A, *) via the API, see it listed with the NR label,
    revoke it, see it gone."""
    user_id = str(sample_users["user"].id)
    nr_id = str(sample_nominal_roll.id)
    url = f"/api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments"

    created = client.post(url, params=GRANT_SA, json={"unit": "Coy A", "sub_unit_1": "*"})
    assert created.status_code == 201
    body = created.json()
    assert body["unit"] == "Coy A"
    assert body["sub_unit_1"] == "*"

    duplicate = client.post(url, params=GRANT_SA, json={"unit": "Coy A", "sub_unit_1": "*"})
    assert duplicate.status_code == 409

    listed = client.get(
        f"/api/v1/access-control/users/{user_id}/subunit-assignments",
        params={"requesting_user_id": "super-admin-test-id",
                "requesting_user_role": "super_admin"},
    )
    assert listed.status_code == 200
    entries = listed.json()
    assert len(entries) == 1
    assert entries[0]["nominal_roll_label"]  # display label present

    revoked = client.delete(
        f"{url}/{entries[0]['id']}",
        params={"revoked_by": "super-admin-test-id", "user_role": "super_admin"},
    )
    assert revoked.status_code == 200

    listed_again = client.get(
        f"/api/v1/access-control/users/{user_id}/subunit-assignments",
        params={"requesting_user_id": "super-admin-test-id",
                "requesting_user_role": "super_admin"},
    )
    assert listed_again.json() == []


@pytest.mark.asyncio
async def test_admin_users_page_shows_scopes_without_toggle(
    client: TestClient, db_session, sample_nominal_roll, sample_personnel,
    sample_users,
):
    """The /admin/users table renders each user's grants directly (issue 28
    follow-up) — no Scope-panel interaction needed to see access state."""
    from parade_state.auth.session import create_user_session
    from parade_state.utils.cookies import AUTH_COOKIE_NAME

    await _grant(
        db_session, sample_nominal_roll.id, str(sample_users["user"].id),
        "Coy A", "Platoon 1",
    )

    session = await create_user_session(
        db_session,
        user_id="super-admin-test-id",
        email="super-admin-test@example.com",
        name="super-admin-test",
        role="super_admin",
    )
    await db_session.commit()
    client.cookies.set(AUTH_COOKIE_NAME, session.token)

    response = client.get("/admin/users")
    assert response.status_code == 200
    body = response.text
    assert "Access Scopes" in body
    # The granted chip is server-rendered in the table itself.
    assert "Platoon 1" in body
    assert "Coy A" in body
