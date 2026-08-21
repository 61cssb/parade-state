"""Tests for the super-admin data purge endpoint (testing-only feature).

The purge deletes every nominal roll and all downstream data in one
transaction while preserving users, access levels, sessions, global
column mappings, and the audit log (the purge itself is logged).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.config import get_settings
from parade_state.models import (
    AccessLevel,
    Attendance,
    AuditLog,
    ColumnMapping,
    CsvUpload,
    Grouping,
    GroupingGroup,
    GroupingMemberState,
    GroupingMembership,
    NominalRoll,
    Personnel,
    Tagging,
    User,
    UserSession,
    UserSubunitAssignment,
)

PURGE_URL = "/api/v1/admin/purge"

SUPER_ADMIN_PARAMS = {"user_id": "super-admin-test-id", "user_role": "super_admin"}
ADMIN_PARAMS = {"user_id": "admin-user-id", "user_role": "admin"}


@pytest.fixture
async def seeded_downstream_data(
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_grouping,
    sample_attendance,
):
    """Add NR-linked rows the standard fixtures don't cover.

    Everything is tied to the sample nominal roll, so a successful purge
    must delete all of it.
    """
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_personnel[0].nominal_roll_id)

    upload = CsvUpload(
        nominal_roll_id=nr_id,
        raw_content=b"pers_no,rank\n1,PTE\n",
        sha256_hash="purge-test-hash",
        line_count=1,
        uploaded_by=admin_id,
        status="received",
    )
    tagging = Tagging(label="purge-test", nominal_roll_id=nr_id, created_by=admin_id)
    pre_existing_audit = AuditLog(
        user_id=admin_id,
        entity_type="user",
        entity_id=admin_id,
        action="create",
        description="pre-existing audit entry that must survive the purge",
    )
    column_mapping = ColumnMapping(raw_name="pers_no", canonical_name="pers_no")

    db_session.add_all([upload, tagging, pre_existing_audit, column_mapping])
    await db_session.commit()

    return {
        "upload": upload,
        "tagging": tagging,
        "audit": pre_existing_audit,
        "column_mapping": column_mapping,
    }


@pytest.mark.asyncio
async def test_purge_forbidden_for_plain_admin(client: TestClient):
    """Non-super-admins get 403 before anything else happens."""
    response = client.post(
        PURGE_URL, params={**ADMIN_PARAMS, "confirmation": "PURGE"}
    )
    assert response.status_code == 403
    assert "super admin" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_purge_rejects_wrong_confirmation(client: TestClient):
    """The type-to-confirm guard must match the exact word."""
    response = client.post(
        PURGE_URL, params={**SUPER_ADMIN_PARAMS, "confirmation": "purge"}
    )
    assert response.status_code == 400
    assert "PURGE" in response.json()["detail"]


@pytest.mark.asyncio
async def test_purge_disabled_deployment(client: TestClient, monkeypatch):
    """PURGE_ENABLED=false short-circuits with 400."""
    monkeypatch.setattr(get_settings(), "PURGE_ENABLED", False)
    response = client.post(
        PURGE_URL, params={**SUPER_ADMIN_PARAMS, "confirmation": "PURGE"}
    )
    assert response.status_code == 400
    assert "PURGE_ENABLED" in response.json()["detail"]


@pytest.mark.asyncio
async def test_purge_deletes_downstream_and_preserves_the_rest(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_attendance,
    seeded_downstream_data,
):
    """Happy path: NRs and downstream data vanish, config/users/audit stay."""
    admin_id = str(sample_users["admin"].id)

    async def count(model) -> int:
        return (await db_session.scalar(select(func.count()).select_from(model))) or 0

    # Preserve baselines: users include the 5 autouse well-known identities,
    # access levels and column mappings must survive the purge untouched.
    users_before = await count(User)
    levels_before = await count(AccessLevel)
    mappings_before = await count(ColumnMapping)
    assert users_before >= 3  # sample admin+user plus well-known identities

    response = client.post(
        PURGE_URL,
        params={"user_id": admin_id, "user_role": "super_admin", "confirmation": "PURGE"},
    )
    assert response.status_code == 200
    body = response.json()
    counts = body["purged_counts"]
    assert counts["nominal_rolls"] == 1
    assert counts["personnel"] == 3
    assert counts["attendance"] == 3
    assert counts["groupings"] == 1
    assert counts["csv_uploads"] == 1
    assert counts["taggings"] == 1

    # Downstream data is gone.
    for model in (
        NominalRoll,
        Personnel,
        Attendance,
        Grouping,
        GroupingGroup,
        GroupingMembership,
        GroupingMemberState,
        CsvUpload,
        Tagging,
        UserSubunitAssignment,
    ):
        assert await count(model) == 0, f"{model.__name__} should be empty"

    # Users, access levels, sessions, and column mappings survive.
    assert await count(User) == users_before
    assert await count(AccessLevel) == levels_before
    assert await count(UserSession) == 0
    assert await count(ColumnMapping) == mappings_before

    # Audit log preserved, and the purge itself is recorded with counts.
    audit_entries = (
        (await db_session.execute(select(AuditLog).order_by(AuditLog.timestamp)))
        .scalars()
        .all()
    )
    assert len(audit_entries) == 2
    purge_entry = audit_entries[-1]
    assert purge_entry.entity_type == "database"
    assert purge_entry.entity_id == "purge"
    assert purge_entry.action == "delete"
    assert purge_entry.user_id == admin_id
    assert '"nominal_rolls": 1' in purge_entry.description
    assert audit_entries[0].id == seeded_downstream_data["audit"].id


@pytest.mark.asyncio
async def test_purge_on_empty_database_is_a_no_op(client: TestClient, sample_users):
    """Purging with nothing seeded succeeds and reports zero counts."""
    admin_id = str(sample_users["admin"].id)
    response = client.post(
        PURGE_URL,
        params={"user_id": admin_id, "user_role": "super_admin", "confirmation": "PURGE"},
    )
    assert response.status_code == 200
    assert response.json()["purged_counts"]["nominal_rolls"] == 0
