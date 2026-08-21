"""Groupings API integration tests (issue 26 redesign).

A grouping is a labelled set of group enums on the attendance-active
nominal roll, with memberships and per-serviceman checkbox / remarks.
Groupings never interact with attendance — the export carries no
attendance columns and no endpoint here touches Attendance.

Mutation endpoints are super-admin only; reads are open to every role.
"""

import csv
import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import (
    Grouping,
    GroupingGroup,
    GroupingMemberState,
    GroupingMembership,
    NominalRoll,
    Personnel,
)
from parade_state.utils import utc_dt

SA = {"user_id": "super-admin-test-id", "user_role": "super_admin"}
ADMIN = {"user_id": "admin-id", "user_role": "admin"}
USER = {"user_id": "user-id", "user_role": "user"}

BASE = "/api/v1/groupings"


def _groups_by_label(response_body: dict) -> dict[str, dict]:
    return {g["label"]: g for g in response_body["groups"]}


# ============================================================================
# Create
# ============================================================================


@pytest.mark.asyncio
async def test_create_grouping_on_active_nr(
    client: TestClient, sample_attendance_scope
):
    response = client.post(
        f"{BASE}/",
        params=SA,
        json={
            "label": "Duty Groups",
            "groups": [{"label": "Grp 10"}, {"label": "Grp 2"}],
            "multiple_membership": False,
            "allow_ungrouped": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["label"] == "Duty Groups"
    assert body["nominal_roll_id"] == str(sample_attendance_scope.id)
    assert body["multiple_membership"] is False
    assert body["allow_ungrouped"] is True
    # Display order follows the payload order, not alphabetical.
    assert [g["label"] for g in body["groups"]] == ["Grp 10", "Grp 2"]
    assert [g["position"] for g in body["groups"]] == [0, 1]


@pytest.mark.asyncio
async def test_create_grouping_requires_active_nr(
    client: TestClient, sample_nominal_roll
):
    response = client.post(
        f"{BASE}/",
        params=SA,
        json={"label": "No Roll", "groups": []},
    )
    assert response.status_code == 400
    assert "active for attendance" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_grouping_duplicate_label(
    client: TestClient, sample_attendance_scope
):
    first = client.post(f"{BASE}/", params=SA, json={"label": "Same"})
    assert first.status_code == 201
    second = client.post(f"{BASE}/", params=SA, json={"label": "Same"})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_create_grouping_duplicate_group_labels(
    client: TestClient, sample_attendance_scope
):
    response = client.post(
        f"{BASE}/",
        params=SA,
        json={
            "label": "Dupes",
            "groups": [{"label": "A"}, {"label": "A"}],
        },
    )
    assert response.status_code == 400
    assert "Duplicate group label" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_grouping_rejects_bad_charset(
    client: TestClient, sample_attendance_scope
):
    response = client.post(
        f"{BASE}/",
        params=SA,
        json={"label": "Bad <script>", "groups": []},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_grouping_non_super_admin_forbidden(
    client: TestClient, sample_attendance_scope
):
    for params in (ADMIN, USER):
        response = client.post(f"{BASE}/", params=params, json={"label": "X"})
        assert response.status_code == 403


# ============================================================================
# List / get — active-NR reachability
# ============================================================================


@pytest.mark.asyncio
async def test_list_scopes_to_active_nr(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_users,
):
    # A grouping on a different (non-active) roll must not be listed.
    other_roll = NominalRoll(
        caa=date(2024, 2, 1),
        csv_hash="other-hash",
        personnel_count=0,
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(other_roll)
    await db_session.flush()
    db_session.add(
        Grouping(
            label="Other Roll Grouping",
            nominal_roll_id=str(other_roll.id),
            created_by=str(sample_users["admin"].id),
        )
    )
    await db_session.commit()

    response = client.get(f"{BASE}/", params=USER)
    assert response.status_code == 200
    labels = [g["label"] for g in response.json()]
    assert labels == ["Test Grouping"]


@pytest.mark.asyncio
async def test_list_empty_without_active_nr(
    client: TestClient, sample_nominal_roll
):
    response = client.get(f"{BASE}/", params=USER)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_grouping_includes_member_counts(
    client: TestClient, sample_attendance_scope, sample_grouping,
    sample_grouping_memberships,
):
    response = client.get(f"{BASE}/{sample_grouping.id}", params=USER)
    assert response.status_code == 200
    groups = _groups_by_label(response.json())
    assert groups["Grp 1"]["member_count"] == 1
    assert groups["Grp 2"]["member_count"] == 1


@pytest.mark.asyncio
async def test_get_grouping_on_non_active_nr_404(
    client: TestClient, sample_grouping, sample_nominal_roll,
):
    # sample_grouping's roll is not active for attendance.
    response = client.get(f"{BASE}/{sample_grouping.id}", params=SA)
    assert response.status_code == 404


# ============================================================================
# Patch — label, group set, flag immutability
# ============================================================================


@pytest.mark.asyncio
async def test_patch_renames_label(
    client: TestClient, sample_attendance_scope, sample_grouping
):
    response = client.patch(
        f"{BASE}/{sample_grouping.id}", params=SA, json={"label": "Renamed"}
    )
    assert response.status_code == 200
    assert response.json()["label"] == "Renamed"


@pytest.mark.asyncio
async def test_patch_label_conflict(
    client: TestClient, sample_attendance_scope, sample_grouping
):
    client.post(f"{BASE}/", params=SA, json={"label": "Taken"})
    response = client.patch(
        f"{BASE}/{sample_grouping.id}", params=SA, json={"label": "Taken"}
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_patch_flags_immutable(
    client: TestClient, sample_attendance_scope, sample_grouping
):
    for payload in (
        {"multiple_membership": True},
        {"allow_ungrouped": False},
    ):
        response = client.patch(
            f"{BASE}/{sample_grouping.id}", params=SA, json=payload
        )
        assert response.status_code == 400
        assert "cannot be changed after creation" in response.json()["detail"]


@pytest.mark.asyncio
async def test_patch_group_set_rename_reorder_add(
    client: TestClient, sample_attendance_scope, sample_grouping
):
    detail = client.get(f"{BASE}/{sample_grouping.id}", params=SA).json()
    existing = _groups_by_label(detail)

    response = client.patch(
        f"{BASE}/{sample_grouping.id}",
        params=SA,
        json={
            "groups": [
                {"id": existing["Grp 2"]["id"], "label": "Second"},   # rename + move up
                {"id": existing["Grp 1"]["id"], "label": "Grp 1"},    # unchanged, moved down
                {"label": "Brand New"},                               # addition
            ]
        },
    )
    assert response.status_code == 200, response.text
    groups = response.json()["groups"]
    assert [(g["label"], g["position"]) for g in groups] == [
        ("Second", 0),
        ("Grp 1", 1),
        ("Brand New", 2),
    ]


@pytest.mark.asyncio
async def test_patch_rename_propagates_to_memberships(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_grouping_memberships,
):
    detail = client.get(f"{BASE}/{sample_grouping.id}", params=SA).json()
    existing = _groups_by_label(detail)
    response = client.patch(
        f"{BASE}/{sample_grouping.id}",
        params=SA,
        json={"groups": [
            {"id": existing["Grp 1"]["id"], "label": "Renamed 1"},
            {"id": existing["Grp 2"]["id"], "label": "Grp 2"},
        ]},
    )
    assert response.status_code == 200
    grouping_id = str(sample_grouping.id)
    db_session.expire_all()

    # The membership still points at the renamed group row.
    memberships = (
        (await db_session.execute(select(GroupingMembership).where(
            GroupingMembership.grouping_id == grouping_id)))
    ).scalars().all()
    assert len(memberships) == 2
    labels = {
        str(g.id): g.label
        for g in (await db_session.execute(select(GroupingGroup))).scalars().all()
    }
    assert {labels[m.group_id] for m in memberships} == {"Renamed 1", "Grp 2"}


@pytest.mark.asyncio
async def test_patch_group_removal_cascades_memberships(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_grouping_memberships,
):
    detail = client.get(f"{BASE}/{sample_grouping.id}", params=SA).json()
    existing = _groups_by_label(detail)
    response = client.patch(
        f"{BASE}/{sample_grouping.id}",
        params=SA,
        json={"groups": [{"id": existing["Grp 2"]["id"], "label": "Grp 2"}]},
    )
    assert response.status_code == 200

    memberships = (
        (await db_session.execute(select(GroupingMembership).where(
            GroupingMembership.grouping_id == sample_grouping.id)))
    ).scalars().all()
    # Grp 1's member became ungrouped; only Grp 2's member remains.
    assert len(memberships) == 1
    assert memberships[0].group_id == existing["Grp 2"]["id"]


@pytest.mark.asyncio
async def test_patch_group_removal_blocked_when_ungrouped_not_allowed(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_users, sample_personnel,
):
    strict = Grouping(
        label="Strict",
        nominal_roll_id=str(sample_attendance_scope.id),
        multiple_membership=False,
        allow_ungrouped=False,
        created_by=str(sample_users["admin"].id),
    )
    group = GroupingGroup(label="Only", position=0)
    strict.groups.append(group)
    db_session.add(strict)
    await db_session.flush()
    db_session.add(
        GroupingMembership(
            grouping_id=strict.id,
            group_id=group.id,
            personnel_id=str(sample_personnel[0].id),
        )
    )
    await db_session.commit()

    response = client.patch(
        f"{BASE}/{strict.id}", params=SA, json={"groups": []}
    )
    assert response.status_code == 400
    assert "would be left" in response.json()["detail"]


@pytest.mark.asyncio
async def test_patch_unknown_group_id_rejected(
    client: TestClient, sample_attendance_scope, sample_grouping
):
    response = client.patch(
        f"{BASE}/{sample_grouping.id}",
        params=SA,
        json={"groups": [{"id": "not-a-known-id", "label": "X"}]},
    )
    assert response.status_code == 400


# ============================================================================
# Delete
# ============================================================================


@pytest.mark.asyncio
async def test_delete_grouping_cascades(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_grouping_memberships, sample_personnel,
):
    db_session.add(
        GroupingMemberState(
            grouping_id=sample_grouping.id,
            personnel_id=str(sample_personnel[0].id),
            checkbox=True,
            remarks="note",
            updated_by=str(sample_personnel[0].created_by),
        )
    )
    await db_session.commit()

    response = client.delete(f"{BASE}/{sample_grouping.id}", params=SA)
    assert response.status_code == 204

    for model in (Grouping, GroupingGroup, GroupingMembership, GroupingMemberState):
        rows = (await db_session.execute(select(model))).scalars().all()
        assert rows == [], f"{model.__tablename__} should be empty"


@pytest.mark.asyncio
async def test_delete_grouping_non_super_admin_forbidden(
    client: TestClient, sample_attendance_scope, sample_grouping
):
    for params in (ADMIN, USER):
        response = client.delete(f"{BASE}/{sample_grouping.id}", params=params)
        assert response.status_code == 403


# ============================================================================
# Memberships
# ============================================================================


@pytest.mark.asyncio
async def test_set_personnel_groups_round_trip(
    client: TestClient, sample_attendance_scope, sample_grouping,
    sample_personnel,
):
    groups = _groups_by_label(client.get(f"{BASE}/{sample_grouping.id}", params=SA).json())
    pid = str(sample_personnel[0].id)

    response = client.put(
        f"{BASE}/{sample_grouping.id}/personnel/{pid}/groups",
        params=SA,
        json={"group_ids": [groups["Grp 2"]["id"]]},
    )
    assert response.status_code == 200
    assert _groups_by_label(response.json())["Grp 2"]["member_count"] == 1

    # Moving to another group is a set replace.
    response = client.put(
        f"{BASE}/{sample_grouping.id}/personnel/{pid}/groups",
        params=SA,
        json={"group_ids": [groups["Grp 1"]["id"]]},
    )
    assert response.status_code == 200
    body = _groups_by_label(response.json())
    assert body["Grp 1"]["member_count"] == 1
    assert body["Grp 2"]["member_count"] == 0


@pytest.mark.asyncio
async def test_second_group_rejected_without_multiple_membership(
    client: TestClient, sample_attendance_scope, sample_grouping,
    sample_personnel,
):
    groups = _groups_by_label(client.get(f"{BASE}/{sample_grouping.id}", params=SA).json())
    response = client.put(
        f"{BASE}/{sample_grouping.id}/personnel/{sample_personnel[0].id}/groups",
        params=SA,
        json={"group_ids": [groups["Grp 1"]["id"], groups["Grp 2"]["id"]]},
    )
    assert response.status_code == 400
    assert "only one group" in response.json()["detail"]


@pytest.mark.asyncio
async def test_multiple_groups_allowed_when_enabled(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_users, sample_personnel,
):
    multi = Grouping(
        label="Multi",
        nominal_roll_id=str(sample_attendance_scope.id),
        multiple_membership=True,
        allow_ungrouped=True,
        created_by=str(sample_users["admin"].id),
    )
    multi.groups.append(GroupingGroup(label="A", position=0))
    multi.groups.append(GroupingGroup(label="B", position=1))
    db_session.add(multi)
    await db_session.commit()

    ids = [str(g.id) for g in multi.groups]
    response = client.put(
        f"{BASE}/{multi.id}/personnel/{sample_personnel[0].id}/groups",
        params=SA,
        json={"group_ids": ids},
    )
    assert response.status_code == 200
    counts = {g["label"]: g["member_count"] for g in response.json()["groups"]}
    assert counts == {"A": 1, "B": 1}


@pytest.mark.asyncio
async def test_empty_set_rejected_when_ungrouped_not_allowed(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_users, sample_personnel,
):
    strict = Grouping(
        label="Strict Roster",
        nominal_roll_id=str(sample_attendance_scope.id),
        multiple_membership=False,
        allow_ungrouped=False,
        created_by=str(sample_users["admin"].id),
    )
    strict.groups.append(GroupingGroup(label="S1", position=0))
    db_session.add(strict)
    await db_session.commit()

    response = client.put(
        f"{BASE}/{strict.id}/personnel/{sample_personnel[0].id}/groups",
        params=SA,
        json={"group_ids": []},
    )
    assert response.status_code == 400
    assert "requires every serviceman" in response.json()["detail"]


@pytest.mark.asyncio
async def test_set_groups_unknown_group_id(
    client: TestClient, sample_attendance_scope, sample_grouping,
    sample_personnel,
):
    response = client.put(
        f"{BASE}/{sample_grouping.id}/personnel/{sample_personnel[0].id}/groups",
        params=SA,
        json={"group_ids": ["bogus"]},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_set_groups_personnel_from_other_roll_404(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_users,
):
    other_roll = NominalRoll(
        caa=date(2024, 6, 1),
        csv_hash="hash-two",
        personnel_count=1,
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(other_roll)
    await db_session.flush()
    outsider = Personnel(
        nominal_roll_id=str(other_roll.id),
        pers_no="99999999",
        rank="PTE",
        category="WOSE",
        full_name="Outsider",
        unit="Coy Z",
        created_by=str(sample_users["admin"].id),
    )
    db_session.add(outsider)
    await db_session.commit()

    response = client.put(
        f"{BASE}/{sample_grouping.id}/personnel/{str(outsider.id)}/groups",
        params=SA,
        json={"group_ids": []},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_set_groups_non_super_admin_forbidden(
    client: TestClient, sample_attendance_scope, sample_grouping,
    sample_personnel,
):
    for params in (ADMIN, USER):
        response = client.put(
            f"{BASE}/{sample_grouping.id}/personnel/{sample_personnel[0].id}/groups",
            params=params,
            json={"group_ids": []},
        )
        assert response.status_code == 403


# ============================================================================
# Member state (checkbox / remarks)
# ============================================================================


@pytest.mark.asyncio
async def test_member_state_upsert_and_clear(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_personnel,
):
    pid = str(sample_personnel[0].id)
    grouping_id = str(sample_grouping.id)
    url = f"{BASE}/{grouping_id}/personnel/{pid}/state"

    response = client.patch(url, params=SA, json={"checkbox": True, "remarks": "driver"})
    assert response.status_code == 200

    async def _load_state() -> GroupingMemberState | None:
        return (
            await db_session.execute(
                select(GroupingMemberState).where(
                    GroupingMemberState.grouping_id == grouping_id,
                    GroupingMemberState.personnel_id == pid,
                )
            )
        ).scalar_one_or_none()

    state = await _load_state()
    assert state is not None
    assert state.checkbox is True
    assert state.remarks == "driver"

    # Empty string clears remarks; checkbox untouched.
    response = client.patch(url, params=SA, json={"remarks": ""})
    assert response.status_code == 200
    db_session.expire_all()
    state = await _load_state()
    assert state.checkbox is True
    assert state.remarks is None


@pytest.mark.asyncio
async def test_member_state_non_super_admin_forbidden(
    client: TestClient, sample_attendance_scope, sample_grouping,
    sample_personnel,
):
    response = client.patch(
        f"{BASE}/{sample_grouping.id}/personnel/{sample_personnel[0].id}/state",
        params=ADMIN,
        json={"checkbox": True},
    )
    assert response.status_code == 403


# ============================================================================
# Clone
# ============================================================================


@pytest.mark.asyncio
async def test_clone_structure_only(
    client: TestClient, sample_attendance_scope,
    sample_grouping, sample_grouping_memberships,
):
    response = client.post(
        f"{BASE}/{sample_grouping.id}/clone",
        params=SA,
        json={"label": "Test Grouping (copy)", "include_memberships": False},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["label"] == "Test Grouping (copy)"
    assert [g["label"] for g in body["groups"]] == ["Grp 1", "Grp 2"]
    assert all(g["member_count"] == 0 for g in body["groups"])


@pytest.mark.asyncio
async def test_clone_with_memberships_and_state(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_grouping_memberships, sample_personnel, sample_users,
):
    db_session.add(
        GroupingMemberState(
            grouping_id=sample_grouping.id,
            personnel_id=str(sample_personnel[0].id),
            checkbox=True,
            remarks="keep me",
            updated_by=str(sample_users["admin"].id),
        )
    )
    await db_session.commit()

    response = client.post(
        f"{BASE}/{sample_grouping.id}/clone",
        params=SA,
        json={"label": "With Members", "include_memberships": True},
    )
    assert response.status_code == 201
    clone_id = response.json()["id"]

    memberships = (
        (await db_session.execute(select(GroupingMembership).where(
            GroupingMembership.grouping_id == clone_id)))
    ).scalars().all()
    assert len(memberships) == 2
    states = (
        (await db_session.execute(select(GroupingMemberState).where(
            GroupingMemberState.grouping_id == clone_id)))
    ).scalars().all()
    assert len(states) == 1
    assert states[0].remarks == "keep me"


@pytest.mark.asyncio
async def test_clone_duplicate_label_409(
    client: TestClient, sample_attendance_scope, sample_grouping
):
    response = client.post(
        f"{BASE}/{sample_grouping.id}/clone",
        params=SA,
        json={"label": "Test Grouping"},
    )
    assert response.status_code == 409


# ============================================================================
# Copy from previous NR
# ============================================================================


async def _activate_roll(db_session: AsyncSession, roll: NominalRoll) -> None:
    """Make ``roll`` the attendance-active NR, deactivating others."""
    others = (
        await db_session.execute(
            select(NominalRoll).where(NominalRoll.attendance_active.is_(True))
        )
    ).scalars().all()
    for other in others:
        other.attendance_active = False
    roll.attendance_active = True
    roll.attendance_activated_at = utc_dt.ensure_naive(utc_dt.utcnow())
    await db_session.commit()


@pytest.mark.asyncio
async def test_copy_from_previous_relinks_by_pers_no(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_grouping_memberships, sample_personnel,
    sample_users,
):
    # New roll: person 0's pers_no matches, person 1 does not, plus a
    # newcomer with no counterpart.
    new_roll = NominalRoll(
        caa=sample_attendance_scope.caa + timedelta(days=31),
        csv_hash="new-hash",
        personnel_count=3,
        uploaded_by=str(sample_users["admin"].id),
    )
    db_session.add(new_roll)
    await db_session.flush()
    matched = Personnel(
        nominal_roll_id=str(new_roll.id),
        pers_no=sample_personnel[0].pers_no,
        rank="PTE",
        category="WOSE",
        full_name="John Doe (new cycle)",
        unit="Coy A",
        created_by=str(sample_users["admin"].id),
    )
    unmatched = Personnel(
        nominal_roll_id=str(new_roll.id),
        pers_no="77777777",
        rank="LCP",
        category="WOSE",
        full_name="Fresh Face",
        unit="Coy A",
        created_by=str(sample_users["admin"].id),
    )
    db_session.add_all([matched, unmatched])
    await db_session.commit()
    await _activate_roll(db_session, new_roll)

    response = client.post(
        f"{BASE}/copy-from-previous",
        params=SA,
        json={"source_grouping_id": str(sample_grouping.id)},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["label"] == "Test Grouping"
    assert body["nominal_roll_id"] == str(new_roll.id)
    counts = {g["label"]: g["member_count"] for g in body["groups"]}
    # Only the pers_no-matched person carried their Grp 1 membership over;
    # Grp 2's member has no counterpart on the new roll.
    assert counts == {"Grp 1": 1, "Grp 2": 0}


@pytest.mark.asyncio
async def test_copy_from_previous_label_collision(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_users,
):
    # Previous roll (activated earlier, now inactive) carrying the source.
    previous_roll = NominalRoll(
        caa=date(2023, 12, 1),
        csv_hash="prev-hash",
        personnel_count=0,
        uploaded_by=str(sample_users["admin"].id),
        attendance_activated_at=utc_dt.ensure_naive(
            utc_dt.utcnow() - timedelta(days=1)
        ),
    )
    db_session.add(previous_roll)
    await db_session.flush()
    source = Grouping(
        label="Old Label",
        nominal_roll_id=str(previous_roll.id),
        created_by=str(sample_users["admin"].id),
    )
    source.groups.append(GroupingGroup(label="G1", position=0))
    db_session.add(source)
    # Occupy the same label on the ACTIVE roll.
    db_session.add(
        Grouping(
            label="Old Label",
            nominal_roll_id=str(sample_attendance_scope.id),
            created_by=str(sample_users["admin"].id),
        )
    )
    await db_session.commit()

    response = client.post(
        f"{BASE}/copy-from-previous",
        params=SA,
        json={"source_grouping_id": str(source.id)},
    )
    assert response.status_code == 409, response.text

    explicit = client.post(
        f"{BASE}/copy-from-previous",
        params=SA,
        json={
            "source_grouping_id": str(source.id),
            "label": "Old Label 2026-09",
        },
    )
    assert explicit.status_code == 201
    assert explicit.json()["label"] == "Old Label 2026-09"


@pytest.mark.asyncio
async def test_copy_from_previous_without_previous_roll(
    client: TestClient, sample_attendance_scope, sample_grouping
):
    response = client.post(
        f"{BASE}/copy-from-previous",
        params=SA,
        json={"source_grouping_id": str(sample_grouping.id)},
    )
    # The source grouping is on the active roll itself; no grouping on a
    # previously activated roll matches, so the copy cannot proceed.
    assert response.status_code in (400, 404)


# ============================================================================
# CSV export
# ============================================================================


@pytest.mark.asyncio
async def test_export_csv_columns_and_content(
    client: TestClient, db_session: AsyncSession, sample_attendance_scope,
    sample_grouping, sample_grouping_memberships, sample_personnel,
    sample_users,
):
    db_session.add(
        GroupingMemberState(
            grouping_id=sample_grouping.id,
            personnel_id=str(sample_personnel[0].id),
            checkbox=True,
            remarks="exported remark",
            updated_by=str(sample_users["admin"].id),
        )
    )
    await db_session.commit()

    response = client.get(f"{BASE}/{sample_grouping.id}/export", params=USER)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == [
        "Group", "Rank", "Name", "Unit", "Sub Unit", "Checkbox", "Remarks",
    ]
    by_name = {row[2]: row for row in rows[1:]}
    assert by_name["John Doe"][0] == "Grp 1"
    assert by_name["John Doe"][5] == "Yes"
    assert by_name["John Doe"][6] == "exported remark"
    assert by_name["Jane Smith"][0] == "Grp 2"
    # Ungrouped serviceman exports with an empty group cell.
    assert by_name["Bob Johnson"][0] == ""
    # No attendance columns anywhere in the header.
    header = " ".join(rows[0]).lower()
    assert "attendance" not in header
    assert "am" not in header.split()
    assert "pm" not in header.split()
