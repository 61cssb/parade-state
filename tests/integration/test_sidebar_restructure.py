"""Tests for the sidebar restructure (issue 07).

Covers the sidebar layout (workflow pages flat, then an Admin section),
the in-page no-access pattern for super-admin-only pages, the merged
management elements on /nominal-roll and /grouping, the moved Manage
Personnel page, and the retired admin pages (/admin/nominal-rolls,
/admin/groupings, /admin/sessions).
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
async def test_sidebar_lists_workflow_pages_then_admin_section(
    client: TestClient, db_session: AsyncSession, sample_users
):
    """The sidebar lists the workflow pages flat (Dashboard through Grouping,
    in order), then an Admin section with Users, Settings, Audit Log, and
    the relabelled Restore Backup entry."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = client.get("/admin")

    assert response.status_code == 200
    body = response.text

    # Workflow pages in order, no section label above them.
    order = [
        'href="/admin"',
        'href="/admin/csv-upload"',
        'href="/nominal-roll"',
        'href="/admin/taggings"',
        'href="/admin/deferments"',
        'href="/attendance"',
        'href="/grouping"',
        'nav-section-label">Admin',
        'href="/admin/users"',
        'href="/admin/settings"',
        'href="/admin/audit"',
        'href="/admin/database-restore"',
    ]
    positions = [body.index(marker) for marker in order]
    assert positions == sorted(positions), "sidebar entries out of order"

    assert "Restore Backup" in body
    assert "Upload NR" in body
    assert '<div class="nav-section-label">ICT</div>' not in body
    assert ">DB Restore<" not in body

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
    """Admins get the management expander with label/remarks editing, but
    not the super-admin-only attendance/delete buttons (grouping creation
    moved to the Grouping page — issue 26)."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = client.get("/nominal-roll")

    assert response.status_code == 200
    body = response.text
    assert "Roll management" in body
    assert 'id="label-display"' in body
    assert 'id="remarks-display"' in body
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
async def test_grouping_view_readonly_for_plain_admins(
    client: TestClient, db_session: AsyncSession, sample_users,
    sample_attendance_scope, sample_grouping, sample_personnel,
):
    """Admins see the roster table but no management buttons and no
    inline editors — mutations are super-admin only (server-enforced)."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = client.get("/grouping")

    assert response.status_code == 200
    body = response.text
    assert "Test Grouping" in body  # dropdown option
    assert "John Doe" in body  # roster row
    assert ">New</button>" not in body
    assert ">Edit</button>" not in body
    assert ">Clone</button>" not in body
    assert ">Delete</button>" not in body
    assert 'onchange="onSingleGroupChange' not in body  # no inline editors
    assert 'onchange="onCheckboxChange' not in body


@pytest.mark.asyncio
async def test_grouping_view_super_admin_gets_management_surface(
    client: TestClient, db_session: AsyncSession,
    sample_attendance_scope, sample_grouping, sample_personnel,
):
    """Super admins get the New / Edit / Clone / Delete row and inline
    group / checkbox / remarks editors on the selected grouping."""
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    response = client.get("/grouping")

    assert response.status_code == 200
    body = response.text
    assert ">New</button>" in body
    assert ">Edit</button>" in body
    assert ">Clone</button>" in body
    assert ">Delete</button>" in body
    assert "Export CSV" in body
    assert 'onchange="onSingleGroupChange' in body
    assert 'onchange="onCheckboxChange' in body
    assert 'onchange="onRemarksChange' in body
    # The empty "(no group)" option is offered — allow_ungrouped defaults True.
    assert "<option value=\"\"" in body


@pytest.mark.asyncio
async def test_grouping_personnel_management_route_retired(
    client: TestClient, db_session: AsyncSession, sample_users, sample_grouping
):
    """The old per-grouping personnel management page is gone."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = client.get(f"/grouping/{sample_grouping.id}/personnel")

    assert response.status_code == 404


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
