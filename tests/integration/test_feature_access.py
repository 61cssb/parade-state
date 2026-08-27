"""Feature-access matrix (issue 37).

Role-level visibility configured by super-admins in Settings, stored in
``feature_access``. Effective visibility = ``FEATURE_*`` env flag AND
matrix entry; env-off 404s for everyone, matrix-off 403s for the
configured role only. Absent row = enabled (fail-open), so an empty
matrix behaves exactly like the pre-matrix deployment.

Covers the three enforcement layers — sidebar, page routes, API edges —
plus the hard super-admin gates on Settings and the save endpoint.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.config import get_settings
from parade_state.main import app
from parade_state.models import AuditLog, FeatureAccess


def _seed(key: str, enabled: bool, role: str = "admin") -> FeatureAccess:
    return FeatureAccess(feature_key=key, role=role, enabled=enabled)


async def seed_matrix(db_session: AsyncSession, *rows: FeatureAccess) -> None:
    """Persist matrix rows for one test."""
    db_session.add_all(rows)
    await db_session.commit()


@pytest.fixture
def matrix_off(db_session: AsyncSession):
    """Factory seeding one or more matrix-off rows for the admin role."""

    async def _matrix_off(*keys: str) -> None:
        await seed_matrix(
            db_session,
            *(_seed(key, enabled=False) for key in keys),
        )

    return _matrix_off


# ---------------------------------------------------------------------------
# Fail-open default: empty matrix = today's behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_matrix_admin_visibility_unchanged(client_as):
    """With no matrix rows the admin sees exactly the pre-matrix nav.

    Workflow entries render; the Admin section shows only Audit Log (the
    new super-admin hard gates for Users/Settings/Restore Backup are not
    matrix-driven and always apply).
    """
    admin = await client_as("admin")

    # Upload NR page renders for admins today — must keep doing so.
    response = admin.get("/admin/csv-upload")
    assert response.status_code == 200

    nav = admin.get("/admin/audit")
    assert nav.status_code == 200
    html = nav.text
    assert 'href="/admin/csv-upload"' in html
    assert 'href="/nominal-roll"' in html
    assert 'href="/attendance"' in html
    assert 'href="/admin/audit"' in html
    # Hard-gated Admin-section entries stay hidden for plain admins.
    assert 'href="/admin/settings"' not in html
    assert 'href="/admin/users"' not in html
    assert 'href="/admin/database-restore"' not in html


@pytest.mark.asyncio
async def test_empty_matrix_super_admin_sees_admin_section(client_as):
    """Super-admins always see every sidebar entry, matrix or not."""
    super_admin = await client_as("super_admin")

    nav = super_admin.get("/admin/audit")
    assert nav.status_code == 200
    html = nav.text
    for entry in (
        "/admin/csv-upload",
        "/nominal-roll",
        "/attendance",
        "/admin",
        "/grouping",
        "/admin/users",
        "/admin/settings",
        "/admin/audit",
        "/admin/database-restore",
    ):
        assert f'href="{entry}"' in html


# ---------------------------------------------------------------------------
# upload_nr: the trial's one seeded row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_nr_off_hides_page_and_nav_from_admin(
    client_as, matrix_off
):
    """upload_nr=off: admins lose the page and the sidebar entry;
    super-admins keep both."""
    await matrix_off("upload_nr")

    admin = await client_as("admin")
    assert admin.get("/admin/csv-upload").status_code == 403
    nav = admin.get("/admin/audit")
    assert 'href="/admin/csv-upload"' not in nav.text

    super_admin = await client_as("super_admin")
    assert super_admin.get("/admin/csv-upload").status_code == 200
    assert 'href="/admin/csv-upload"' in super_admin.get("/admin/audit").text


# ---------------------------------------------------------------------------
# Matrix-disabled workflow features: page + API edges refuse admins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nominal_roll_off_refuses_admin_at_page_and_api(
    client_as, matrix_off
):
    await matrix_off("nominal_roll")

    admin = await client_as("admin")
    assert admin.get("/nominal-roll").status_code == 403
    # API edges of the NR browser (router-level matrix gate).
    assert admin.get("/api/v1/personnel").status_code == 403
    assert admin.get("/api/v1/nominal-rolls").status_code == 403
    patch = admin.patch(
        "/api/v1/personnel/00000000-0000-0000-0000-000000000000", json={}
    )
    assert patch.status_code == 403
    assert "not enabled" in patch.json()["detail"]

    super_admin = await client_as("super_admin")
    assert super_admin.get("/nominal-roll").status_code == 200
    assert super_admin.get("/api/v1/personnel").status_code == 200
    assert super_admin.get("/api/v1/nominal-rolls").status_code == 200


@pytest.mark.asyncio
async def test_attendance_off_refuses_admin_at_page_and_api(
    client_as, matrix_off
):
    await matrix_off("attendance")

    admin = await client_as("admin")
    assert admin.get("/attendance").status_code == 403
    assert admin.get("/api/v1/attendance/").status_code == 403

    super_admin = await client_as("super_admin")
    assert super_admin.get("/attendance").status_code == 200


@pytest.mark.asyncio
async def test_grouping_off_refuses_admin_at_page_and_api(client_as, matrix_off):
    await matrix_off("grouping")

    admin = await client_as("admin")
    assert admin.get("/grouping").status_code == 403
    assert admin.get("/api/v1/groupings/").status_code == 403

    super_admin = await client_as("super_admin")
    assert super_admin.get("/grouping").status_code == 200
    assert super_admin.get("/api/v1/groupings/").status_code == 200


@pytest.mark.asyncio
async def test_strength_off_refuses_admin_at_page(client_as, matrix_off):
    await matrix_off("strength")

    admin = await client_as("admin")
    assert admin.get("/admin").status_code == 403

    super_admin = await client_as("super_admin")
    assert super_admin.get("/admin").status_code == 200


# ---------------------------------------------------------------------------
# Env kill switch outranks the matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_flag_off_stays_off_for_everyone(client_as, monkeypatch):
    """FEATURE_* off 404s for both roles even with no matrix row."""
    for settings_obj in {get_settings(), app.state.settings}:
        monkeypatch.setattr(settings_obj, "FEATURE_GROUPING", False)

    admin = await client_as("admin")
    assert admin.get("/grouping").status_code == 404
    assert admin.get("/api/v1/groupings/").status_code == 404

    super_admin = await client_as("super_admin")
    assert super_admin.get("/grouping").status_code == 404
    assert super_admin.get("/api/v1/groupings/").status_code == 404


# ---------------------------------------------------------------------------
# Hard gates: Settings is super-admin-only; audit stays admin-viewable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_page_hard_gated_for_admins(client_as):
    admin = await client_as("admin")
    response = admin.get("/admin/settings")
    assert response.status_code == 403

    super_admin = await client_as("super_admin")
    response = super_admin.get("/admin/settings")
    assert response.status_code == 200
    assert "Feature access (admins)" in response.text


@pytest.mark.asyncio
async def test_audit_page_remains_admin_viewable(client_as):
    """Audit Log is view-only reference — admins keep access (2026-08-27)."""
    admin = await client_as("admin")
    assert admin.get("/admin/audit").status_code == 200


# ---------------------------------------------------------------------------
# Save endpoint
# ---------------------------------------------------------------------------

FULL_ITEMS = [
    {"feature_key": key, "enabled": key != "upload_nr"}
    for key in (
        "upload_nr",
        "nominal_roll",
        "attendance",
        "strength",
        "grouping",
        "deferments",
        "discussions",
    )
]


@pytest.mark.asyncio
async def test_save_feature_access_super_admin_only(client_as):
    admin = await client_as("admin")
    response = admin.post(
        "/api/v1/admin/feature-access", json={"items": FULL_ITEMS}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_save_feature_access_upserts_and_enforces(
    client_as, db_session
):
    super_admin = await client_as("super_admin")
    response = super_admin.post(
        "/api/v1/admin/feature-access", json={"items": FULL_ITEMS}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"
    assert body["features"]["upload_nr"] is False
    assert body["features"]["attendance"] is True

    # Row persisted with the fail-open default elsewhere.
    rows = (
        (await db_session.execute(select(FeatureAccess))).scalars().all()
    )
    by_key = {row.feature_key: row for row in rows}
    assert by_key["upload_nr"].enabled is False
    assert by_key["attendance"].enabled is True

    # The middleware/gates observe the saved matrix on the next request.
    admin = await client_as("admin")
    assert admin.get("/admin/csv-upload").status_code == 403
    assert admin.get("/attendance").status_code == 200

    # Audit trail.
    audit = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.entity_type == "feature_access")
        )
    ).scalars().all()
    assert audit and audit[0].action == "update"
    assert "upload_nr" in (audit[0].changes or "")


@pytest.mark.asyncio
async def test_save_feature_access_rejects_unknown_key(client_as):
    super_admin = await client_as("super_admin")
    response = super_admin.post(
        "/api/v1/admin/feature-access",
        json={"items": [{"feature_key": "nope", "enabled": True}]},
    )
    assert response.status_code == 422
