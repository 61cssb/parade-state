"""Tests for the sidebar restructure (issue 07).

Covers the ICT/Admin section layout, the in-page no-access pattern for
super-admin-only pages, the merged management elements on /nominal-roll
and /grouping, the moved Manage Personnel page, and the retired admin
pages (/admin/nominal-rolls, /admin/groupings, /admin/sessions).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.session import create_user_session
from parade_state.models import User
from parade_state.utils.cookies import AUTH_COOKIE_NAME


async def _sign_in(
    client: TestClient, db_session: AsyncSession, user: User
) -> None:
    """Create a session for ``user`` and set the auth cookie on ``client``."""
    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )
    await db_session.commit()
    client.cookies.set(AUTH_COOKIE_NAME, session.token)


async def _make_super_admin(db_session: AsyncSession) -> User:
    user = User(
        email="sa@example.com", name="Super Admin", status="active", role="super_admin"
    )
    db_session.add(user)
    await db_session.commit()
    return user


# --- Sidebar sections ---


@pytest.mark.asyncio
async def test_sidebar_renders_ict_and_admin_sections(
    client: TestClient, db_session: AsyncSession, sample_users
):
    """The sidebar groups items into ICT and Admin sections and lists every
    page, including the relabelled Upload NR entry."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = client.get("/admin")

    assert response.status_code == 200
    body = response.text
    assert "ICT" in body
    assert "Admin" in body
    for label in (
        "Nominal Roll",
        "Upload NR",
        "Attendance",
        "Grouping",
        "Taggings",
        "Deferments",
        "Dashboard",
        "Users",
        "Settings",
        "Audit Log",
        "DB Restore",
    ):
        assert label in body, label

    # Retired pages must not be linked from the sidebar.
    assert 'href="/admin/nominal-rolls"' not in body
    assert 'href="/admin/groupings"' not in body


@pytest.mark.asyncio
async def test_sidebar_hidden_when_signed_out(client: TestClient):
    """Signed-out visitors get no navigation entries (admin-only system)."""
    response = client.get("/auth/no-access")

    assert response.status_code == 403
    assert 'href="/nominal-roll"' not in response.text


# --- In-page no-access for super-admin-only pages ---


@pytest.mark.asyncio
async def test_sa_pages_show_in_page_no_access_for_plain_admins(
    client: TestClient, db_session: AsyncSession, sample_users
):
    """Plain admins see the page shell with a no-access message (403), not a
    silent redirect away."""
    await _sign_in(client, db_session, sample_users["admin"])

    for path in ("/admin/taggings", "/admin/deferments", "/admin/database-restore"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 403, path
        assert "do not have access to this page" in response.text, path
        # The page shell (sidebar) is still rendered around the message.
        assert "sidebar-nav" in response.text, path
        assert "Contact a super administrator" in response.text, path


@pytest.mark.asyncio
async def test_sa_pages_render_for_super_admins(
    client: TestClient, db_session: AsyncSession, sample_users
):
    """Super admins still get the real pages."""
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    for path in ("/admin/taggings", "/admin/deferments", "/admin/database-restore"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, path


# --- Nominal roll merge ---


@pytest.mark.asyncio
async def test_nominal_roll_view_has_management_element_for_admin(
    client: TestClient, db_session: AsyncSession, sample_users, sample_nominal_roll
):
    """Admins get the management expander with label/remarks editing and
    Create Grouping, but not the super-admin-only attendance/delete buttons."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = client.get("/nominal-roll")

    assert response.status_code == 200
    body = response.text
    assert "Roll management" in body
    assert 'id="label-display"' in body
    assert 'id="remarks-display"' in body
    assert "Create Grouping" in body
    # SA-only action buttons are not rendered (the shared JS helpers may be).
    assert "onclick=\"useForAttendance" not in body
    assert "onclick=\"deactivateAttendance" not in body
    assert "onclick=\"deleteNominalRoll" not in body


@pytest.mark.asyncio
async def test_nominal_roll_view_shows_sa_actions_for_super_admin(
    client: TestClient, db_session: AsyncSession, sample_users, sample_nominal_roll
):
    """Super admins additionally get the attendance toggle and delete."""
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    response = client.get("/nominal-roll")

    assert response.status_code == 200
    body = response.text
    assert "Roll management" in body
    assert "onclick=\"useForAttendance" in body  # roll is not attendance-active
    assert "onclick=\"deleteNominalRoll" in body


# --- Grouping merge ---


@pytest.mark.asyncio
async def test_grouping_view_has_management_element(
    client: TestClient, db_session: AsyncSession, sample_users, sample_grouping
):
    """The active grouping shows metadata, Edit Dates, and Close; draft-only
    Manage Personnel and super-admin Delete stay hidden for a plain admin."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = client.get("/grouping")

    assert response.status_code == 200
    body = response.text
    assert "Grouping management" in body
    assert "standard" in body  # mode chip
    assert "active" in body  # status chip
    assert "Edit Dates" in body  # active + validity window present
    assert "Close" in body
    assert "Manage Personnel" not in body  # draft-only
    assert "onclick=\"deleteGrouping" not in body  # super_admin-only


@pytest.mark.asyncio
async def test_grouping_view_draft_shows_manage_personnel_for_super_admin(
    client: TestClient, db_session: AsyncSession, sample_users, sample_grouping
):
    """A draft grouping exposes Manage Personnel, Activate, and (for super
    admins) Delete — matching the retired admin page's gating."""
    from parade_state.models import Grouping
    from parade_state.utils import utc_dt
    from datetime import timedelta

    draft = Grouping(
        name="Draft Grouping",
        nominal_roll_id=str(sample_grouping.nominal_roll_id),
        mode="standard",
        status="draft",
        valid_from=utc_dt.db_utcnow() - timedelta(days=1),
        valid_until=utc_dt.db_utcnow() + timedelta(days=1),
        personnel_count=3,
        created_by=str(sample_users["admin"].id),
    )
    db_session.add(draft)
    await db_session.commit()

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    response = client.get("/grouping", params={"grouping_id": str(draft.id)})

    assert response.status_code == 200
    body = response.text
    assert "Grouping management" in body
    assert f'href="/grouping/{draft.id}/personnel"' in body
    assert "Activate" in body
    assert "onclick=\"deleteGrouping" in body


# --- Manage Personnel move ---


@pytest.mark.asyncio
async def test_manage_personnel_at_new_route_with_back_link(
    client: TestClient, db_session: AsyncSession, sample_users, sample_grouping
):
    """Manage Personnel lives at /grouping/{id}/personnel and links back to
    /grouping; the old admin route is gone."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = client.get(f"/grouping/{sample_grouping.id}/personnel")

    assert response.status_code == 200
    assert 'href="/grouping"' in response.text
    assert "Back to Grouping" in response.text

    old = client.get(
        f"/admin/groupings/{sample_grouping.id}/personnel", follow_redirects=False
    )
    assert old.status_code == 404


# --- Retired pages ---


@pytest.mark.asyncio
async def test_retired_admin_pages_are_gone(
    client: TestClient, db_session: AsyncSession, sample_users
):
    """/admin/nominal-rolls, /admin/groupings, and /admin/sessions 404."""
    await _sign_in(client, db_session, sample_users["admin"])

    for path in ("/admin/nominal-rolls", "/admin/groupings", "/admin/sessions"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404, path
