"""Behavioral tests for tagging API endpoints.

Covers: super_admin-only authorization, overlay semantics (Personnel rows
never mutate), CRUD with entry validation, and clone behavior (matching by
``short_id``, unmatched surfacing).
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import (
    NominalRoll,
    Personnel,
    Tagging,
    TaggingEntry,
)
from parade_state.utils import utc_dt


# Common query params reused across endpoints.
SUPER_ADMIN_PARAMS = {"user_id": "super-admin-test-id", "user_role": "super_admin"}
ADMIN_PARAMS = {"user_id": "admin-user-id", "user_role": "admin"}
USER_PARAMS = {"user_id": "regular-user-id", "user_role": "user"}


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
async def second_nominal_roll(
    db_session: AsyncSession, sample_users, sample_personnel
) -> tuple[NominalRoll, list[Personnel]]:
    """Create a second NR with two personnel sharing short_ids with the first NR.

    personnel[0] and personnel[1] from the first NR are mirrored here under
    the same short_id (the cross-roll person identifier). personnel[2] is
    intentionally not mirrored — clone tests rely on it being unmatched.
    """
    admin_id = str(sample_users["admin"].id)
    nr = NominalRoll(
        caa=date(2024, 6, 1),
        csv_hash="second_nr_hash",
        status="confirmed",
        personnel_count=2,
        uploaded_by=admin_id,
        confirmed_by=admin_id,
        confirmed_at=utc_dt.utcnow(),
    )
    db_session.add(nr)
    await db_session.flush()  # populate nr.id

    source = sample_personnel  # 3 personnel on the first NR
    mirrored = [
        Personnel(
            nominal_roll_id=str(nr.id),
            short_id=source[0].short_id,  # same person, different NR
            rank=source[0].rank,
            category=source[0].category,
            full_name=source[0].full_name,
            unit="Coy B",  # different subunit on this NR
            sub_unit_1="Platoon 9",
            created_by=admin_id,
        ),
        Personnel(
            nominal_roll_id=str(nr.id),
            short_id=source[1].short_id,
            rank=source[1].rank,
            category=source[1].category,
            full_name=source[1].full_name,
            unit="Coy B",
            sub_unit_1="Platoon 9",
            created_by=admin_id,
        ),
    ]
    for p in mirrored:
        db_session.add(p)
    await db_session.commit()
    return nr, mirrored


# ============================================================================
# Authorization
# ============================================================================


@pytest.mark.asyncio
async def test_admin_role_cannot_list_taggings(
    client: TestClient, admin_token_headers, sample_nominal_roll
):
    """admin role is rejected — super_admin only."""
    response = client.get(
        "/api/v1/taggings",
        headers=admin_token_headers,
        params=ADMIN_PARAMS,
    )
    assert response.status_code == 403
    assert "super admins" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_regular_user_cannot_create_tagging(
    client: TestClient, user_token_headers, sample_nominal_roll
):
    response = client.post(
        "/api/v1/taggings",
        headers=user_token_headers,
        params=USER_PARAMS,
        json={
            "label": "user-attempt",
            "nominal_roll_id": str(sample_nominal_roll.id),
        },
    )
    assert response.status_code == 403


# ============================================================================
# Create
# ============================================================================


@pytest.mark.asyncio
async def test_create_tagging_without_entries(
    client: TestClient, super_admin_token_headers, sample_nominal_roll
):
    response = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "  trimmed  ", "nominal_roll_id": str(sample_nominal_roll.id)},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["label"] == "trimmed"  # label is stripped
    assert data["nominal_roll_id"] == str(sample_nominal_roll.id)
    assert data["entries"] == []
    assert data["created_at"]
    assert data["updated_at"] is None


@pytest.mark.asyncio
async def test_create_tagging_snapshots_from_subunit(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll
):
    """When ``from_*`` omitted, server snapshots the personnel's canonical subunit."""
    p = sample_personnel[0]  # unit=Coy A, sub_unit_1=Platoon 1, sub_unit_2=Section 1
    response = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "cross-attach",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {
                    "personnel_id": str(p.id),
                    "to_unit": "Coy B",
                    "to_sub_unit_1": "Platoon 3",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    entry = response.json()["entries"][0]
    assert entry["from_unit"] == "Coy A"
    assert entry["from_sub_unit_1"] == "Platoon 1"
    assert entry["from_sub_unit_2"] == "Section 1"
    assert entry["to_unit"] == "Coy B"
    assert entry["to_sub_unit_1"] == "Platoon 3"
    assert entry["personnel_short_id"] == p.short_id
    assert "PTE" in (entry["personnel_label"] or "")


@pytest.mark.asyncio
async def test_create_tagging_duplicate_label_409(
    client: TestClient, super_admin_token_headers, sample_nominal_roll
):
    payload = {"label": "same-label", "nominal_roll_id": str(sample_nominal_roll.id)}
    first = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json=payload,
    )
    assert first.status_code == 201

    second = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json=payload,
    )
    assert second.status_code == 409
    assert "same-label" in second.json()["detail"]


@pytest.mark.asyncio
async def test_create_tagging_personnel_not_on_nr_400(
    client: TestClient, super_admin_token_headers, sample_personnel, second_nominal_roll
):
    """Personnel from a different NR is rejected."""
    other_nr, _ = second_nominal_roll
    p_on_first_nr = sample_personnel[0]
    response = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "cross-nr-attempt",
            "nominal_roll_id": str(other_nr.id),
            "entries": [
                {
                    "personnel_id": str(p_on_first_nr.id),
                    "to_unit": "Coy Z",
                }
            ],
        },
    )
    assert response.status_code == 400
    assert "does not belong" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_tagging_duplicate_personnel_in_payload_400(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll
):
    p = sample_personnel[0]
    response = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "dup-person",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": str(p.id), "to_unit": "Coy B"},
                {"personnel_id": str(p.id), "to_unit": "Coy C"},
            ],
        },
    )
    assert response.status_code == 400
    assert "Duplicate personnel" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_tagging_unknown_nominal_roll_404(
    client: TestClient, super_admin_token_headers
):
    response = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "x", "nominal_roll_id": "nonexistent-nr"},
    )
    assert response.status_code == 404


# ============================================================================
# Read
# ============================================================================


@pytest.mark.asyncio
async def test_list_filters_by_nominal_roll(
    client: TestClient, super_admin_token_headers, sample_nominal_roll, second_nominal_roll
):
    other_nr, _ = second_nominal_roll
    client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "on-first", "nominal_roll_id": str(sample_nominal_roll.id)},
    )
    client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "on-second", "nominal_roll_id": str(other_nr.id)},
    )

    response = client.get(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params={**SUPER_ADMIN_PARAMS, "nominal_roll_id": str(sample_nominal_roll.id)},
    )
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["label"] == "on-first"
    # List items don't carry entries; entry_count is computed.
    assert "entries" not in items[0]


@pytest.mark.asyncio
async def test_get_returns_entries(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll
):
    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "with-entries",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": str(sample_personnel[0].id), "to_unit": "Coy B"},
                {"personnel_id": str(sample_personnel[1].id), "to_unit": "Coy C"},
            ],
        },
    )
    tagging_id = create.json()["id"]

    response = client.get(
        f"/api/v1/taggings/{tagging_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["entries"]) == 2


@pytest.mark.asyncio
async def test_get_unknown_tagging_404(
    client: TestClient, super_admin_token_headers
):
    response = client.get(
        "/api/v1/taggings/nonexistent",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
    )
    assert response.status_code == 404


# ============================================================================
# Update
# ============================================================================


@pytest.mark.asyncio
async def test_patch_full_replaces_entries(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll
):
    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "to-update",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": str(sample_personnel[0].id), "to_unit": "Coy B"}
            ],
        },
    )
    tagging_id = create.json()["id"]

    # Full-replace with two different personnel.
    response = client.patch(
        f"/api/v1/taggings/{tagging_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "updated-label",
            "entries": [
                {"personnel_id": str(sample_personnel[1].id), "to_unit": "Coy C"},
                {"personnel_id": str(sample_personnel[2].id), "to_unit": "Coy D"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["label"] == "updated-label"
    assert data["updated_at"] is not None
    personnel_ids = {e["personnel_id"] for e in data["entries"]}
    assert personnel_ids == {
        str(sample_personnel[1].id),
        str(sample_personnel[2].id),
    }


@pytest.mark.asyncio
async def test_patch_duplicate_label_409(
    client: TestClient, super_admin_token_headers, sample_nominal_roll
):
    client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "taken", "nominal_roll_id": str(sample_nominal_roll.id)},
    )
    other = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "free", "nominal_roll_id": str(sample_nominal_roll.id)},
    ).json()["id"]

    response = client.patch(
        f"/api/v1/taggings/{other}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "taken"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_patch_without_entries_preserves_entries(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll
):
    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "keep-entries",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": str(sample_personnel[0].id), "to_unit": "Coy B"}
            ],
        },
    )
    tagging_id = create.json()["id"]

    response = client.patch(
        f"/api/v1/taggings/{tagging_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"remarks": "updated remarks only"},
    )
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 1  # preserved


# ============================================================================
# Delete
# ============================================================================


@pytest.mark.asyncio
async def test_delete_cascades_entries(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll,
    db_session: AsyncSession,
):
    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "doomed",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": str(sample_personnel[0].id), "to_unit": "Coy B"}
            ],
        },
    )
    tagging_id = create.json()["id"]

    response = client.delete(
        f"/api/v1/taggings/{tagging_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
    )
    assert response.status_code == 200

    db_session.expire_all()
    remaining_entries = (
        await db_session.execute(
            select(TaggingEntry).where(TaggingEntry.tagging_id == tagging_id)
        )
    ).all()
    assert remaining_entries == []


@pytest.mark.asyncio
async def test_delete_refuses_when_attendance_linked(
    client: TestClient, super_admin_token_headers, sample_personnel,
    sample_nominal_roll, db_session: AsyncSession,
):
    """A tagging linked to attendance rows cannot be deleted (409)."""
    from parade_state.models import Attendance

    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "linked",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": str(sample_personnel[0].id), "to_unit": "Coy B"}
            ],
        },
    )
    tagging_id = create.json()["id"]

    # Link an attendance row to the tagging.
    db_session.add(
        Attendance(
            personnel_id=str(sample_personnel[0].id),
            nominal_roll_id=str(sample_nominal_roll.id),
            tagging_id=tagging_id,
            date=date.today(),
            status_am="present",
            status_pm="absent",
            created_by="super-admin-test-id",
            updated_by="super-admin-test-id",
        )
    )
    await db_session.commit()

    response = client.delete(
        f"/api/v1/taggings/{tagging_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
    )
    assert response.status_code == 409
    assert "attendance" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_refuses_when_scope_active(
    client: TestClient, super_admin_token_headers, sample_personnel,
    sample_nominal_roll, db_session: AsyncSession,
):
    """A tagging that is the active attendance scope cannot be deleted (409)."""
    from parade_state.models import AttendanceScope

    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "active-scope",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": str(sample_personnel[0].id), "to_unit": "Coy B"}
            ],
        },
    )
    tagging_id = create.json()["id"]

    db_session.add(
        AttendanceScope(
            nominal_roll_id=str(sample_nominal_roll.id),
            tagging_id=tagging_id,
            activated_by="super-admin-test-id",
        )
    )
    await db_session.commit()

    response = client.delete(
        f"/api/v1/taggings/{tagging_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
    )
    assert response.status_code == 409
    assert "scope" in response.json()["detail"].lower()


# ============================================================================
# Overlay semantics
# ============================================================================


@pytest.mark.asyncio
async def test_tagging_does_not_mutate_personnel(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll,
    db_session: AsyncSession,
):
    """Overlay: creating/editing a tagging leaves Personnel rows untouched."""
    p = sample_personnel[0]
    p_id = str(p.id)  # capture before any expire
    original_unit = p.unit
    original_sub1 = p.sub_unit_1

    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "overlay-test",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": p_id, "to_unit": "Remapped Coy", "to_sub_unit_1": "Remapped Plt"},
            ],
        },
    )
    assert create.status_code == 201
    tagging_id = create.json()["id"]

    # Edit (full-replace) to a different remap.
    client.patch(
        f"/api/v1/taggings/{tagging_id}",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "entries": [
                {"personnel_id": p_id, "to_unit": "Another Coy"},
            ],
        },
    )

    db_session.expire_all()
    refreshed = (
        await db_session.execute(
            select(Personnel)
            .where(Personnel.id == p_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.unit == original_unit
    assert refreshed.sub_unit_1 == original_sub1


# ============================================================================
# Clone
# ============================================================================


@pytest.mark.asyncio
async def test_clone_matches_by_short_id(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll,
    second_nominal_roll, db_session: AsyncSession,
):
    """Clone creates a new tagging on the target NR with entries pointing at the
    target-NR personnel rows (matched by short_id). Source has 3 entries, target
    has 2 of those persons mirrored → 2 matched, 1 unmatched.
    """
    other_nr, mirrored = second_nominal_roll
    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "clone-source",
            "nominal_roll_id": str(sample_nominal_roll.id),
            "entries": [
                {"personnel_id": str(sample_personnel[0].id), "to_unit": "Coy X"},
                {"personnel_id": str(sample_personnel[1].id), "to_unit": "Coy Y"},
                {"personnel_id": str(sample_personnel[2].id), "to_unit": "Coy Z"},  # not mirrored
            ],
        },
    )
    source_id = create.json()["id"]

    response = client.post(
        f"/api/v1/taggings/{source_id}/clone",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"target_nominal_roll_id": str(other_nr.id), "label": "clone-target"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source_count"] == 3
    assert data["matched_count"] == 2
    assert len(data["unmatched"]) == 1
    assert data["unmatched"][0]["short_id"] == sample_personnel[2].short_id

    new_tagging = data["tagging"]
    assert new_tagging["nominal_roll_id"] == str(other_nr.id)
    assert new_tagging["label"] == "clone-target"

    # Entries point at TARGET-NR personnel rows (not source).
    target_person_ids = {str(mirrored[0].id), str(mirrored[1].id)}
    cloned_entry_person_ids = {e["personnel_id"] for e in new_tagging["entries"]}
    assert cloned_entry_person_ids == target_person_ids

    # from_* snapshotted from the target NR personnel (Coy B / Platoon 9).
    for entry in new_tagging["entries"]:
        assert entry["from_unit"] == "Coy B"
        assert entry["from_sub_unit_1"] == "Platoon 9"


@pytest.mark.asyncio
async def test_clone_same_nr_400(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll
):
    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "self-clone", "nominal_roll_id": str(sample_nominal_roll.id)},
    )
    source_id = create.json()["id"]

    response = client.post(
        f"/api/v1/taggings/{source_id}/clone",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "target_nominal_roll_id": str(sample_nominal_roll.id),
            "label": "whatever",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_clone_duplicate_label_409(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll,
    second_nominal_roll,
):
    other_nr, _ = second_nominal_roll
    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"label": "shared-label", "nominal_roll_id": str(sample_nominal_roll.id)},
    )
    source_id = create.json()["id"]

    response = client.post(
        f"/api/v1/taggings/{source_id}/clone",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"target_nominal_roll_id": str(other_nr.id), "label": "shared-label"},
    )
    assert response.status_code == 409


# ============================================================================
# Cascade
# ============================================================================


@pytest.mark.asyncio
async def test_delete_nominal_roll_cascades_taggings(
    client: TestClient, super_admin_token_headers, sample_personnel, sample_nominal_roll,
    db_session: AsyncSession,
):
    nr_id = str(sample_nominal_roll.id)
    create = client.post(
        "/api/v1/taggings",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "label": "cascade-test",
            "nominal_roll_id": nr_id,
            "entries": [
                {"personnel_id": str(sample_personnel[0].id), "to_unit": "Coy B"}
            ],
        },
    )
    tagging_id = create.json()["id"]

    # Delete the NR via ORM to exercise the relationship cascade
    # (cascade="all, delete-orphan" on NominalRoll.taggings).
    nr = (
        await db_session.execute(select(NominalRoll).where(NominalRoll.id == nr_id))
    ).scalar_one()
    await db_session.delete(nr)
    await db_session.commit()

    db_session.expire_all()
    leftover = (
        await db_session.execute(select(Tagging).where(Tagging.id == tagging_id))
    ).scalar_one_or_none()
    assert leftover is None
