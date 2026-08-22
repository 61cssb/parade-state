"""Feature-flag gating (issue 18).

Flags-off must hide Deferments and Grouping entirely — no nav entry, and
page/API routes unreachable — for every role including super admins: the
gate sits above role checks so unready features stay invisible during
the tester window even from direct URLs.

The suite-wide default (conftest autouse fixture) runs with flags on,
mirroring the dev environment; these tests flip individual flags to pin
both postures and the prod-default (off) configuration.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.session import create_user_session
from parade_state.config import Settings, get_settings
from parade_state.main import app as main_app
from parade_state.models import User
from parade_state.utils.cookies import AUTH_COOKIE_NAME

SUPER_ADMIN_PARAMS = {"user_id": "super-admin-test-id", "user_role": "super_admin"}


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


def _set_flags(monkeypatch, deferments: bool, grouping: bool) -> None:
    """Point both feature flags at a fixed posture for one test.

    Patches both live Settings objects — the cached instance route
    dependencies read, and the module-level app's ``app.state.settings``
    snapshot the nav templates read — because they diverge once
    test_production_hardening clears the settings cache.
    """
    for settings_obj in {get_settings(), main_app.state.settings}:
        monkeypatch.setattr(settings_obj, "FEATURE_DEFERMENTS", deferments)
        monkeypatch.setattr(settings_obj, "FEATURE_GROUPING", grouping)


# --- Configuration defaults ---


def test_flags_default_off(monkeypatch):
    """With no env vars set (the prod posture) both flags are off."""
    monkeypatch.delenv("FEATURE_DEFERMENTS", raising=False)
    monkeypatch.delenv("FEATURE_GROUPING", raising=False)
    monkeypatch.delenv("FEATURE_DISCUSSIONS", raising=False)

    settings = Settings()

    assert settings.FEATURE_DEFERMENTS is False
    assert settings.FEATURE_GROUPING is False
    assert settings.FEATURE_DISCUSSIONS is False


def test_flags_enabled_via_env_vars(monkeypatch):
    """The dev posture: env vars turn the flags on (no deploy needed)."""
    monkeypatch.setenv("FEATURE_DEFERMENTS", "true")
    monkeypatch.setenv("FEATURE_GROUPING", "true")
    monkeypatch.setenv("FEATURE_DISCUSSIONS", "true")

    settings = Settings()

    assert settings.FEATURE_DEFERMENTS is True
    assert settings.FEATURE_GROUPING is True
    assert settings.FEATURE_DISCUSSIONS is True


# --- Flags off: unreachable for every role, including super admins ---


@pytest.mark.asyncio
async def test_flag_off_hides_nav_and_entry_points(
    client: TestClient,
    db_session: AsyncSession,
    sample_nominal_roll,
    monkeypatch,
):
    """A signed-in super admin sees no trace of either feature: sidebar
    entries, the dashboard Grouping card/button, and the NR browser's
    Create Grouping button/modal are all hidden."""
    _set_flags(monkeypatch, deferments=False, grouping=False)
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert 'href="/admin/deferments"' not in dashboard.text
    assert 'href="/grouping"' not in dashboard.text
    assert "Active Groupings" not in dashboard.text
    assert "New Grouping" not in dashboard.text

    nr_view = client.get("/nominal-roll")
    assert nr_view.status_code == 200
    assert "Create Grouping" not in nr_view.text
    # The modal markup is gone; the shared JS helpers may remain (they are
    # unreachable without the button), as with other gated UI in this app.
    assert 'id="create-modal"' not in nr_view.text


@pytest.mark.asyncio
async def test_flag_off_blocks_pages_even_for_super_admin(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """Direct URLs to flag-off pages 404 for a super admin (and therefore
    for every weaker role too — the gate never consults the role)."""
    _set_flags(monkeypatch, deferments=False, grouping=False)

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    for path in (
        "/admin/deferments",
        "/grouping",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404, path


@pytest.mark.asyncio
async def test_flag_off_page_renders_html_disabled_page(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """Page routes answer with a styled HTML 404 saying the feature is
    deliberately switched off — not a JSON blob, so bookmark/saved-link
    users know it is not a broken URL. Signed-in users keep the app
    shell; anonymous visitors get the same page without the shell."""
    _set_flags(monkeypatch, deferments=False, grouping=False)
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    grouping = client.get("/grouping")
    assert grouping.status_code == 404
    assert grouping.headers["content-type"].startswith("text/html")
    body = grouping.text
    assert "Grouping is switched off on this deployment" in body
    assert "Your link is fine" in body
    assert "sidebar-nav" in body  # app shell renders around the message

    deferments = client.get("/admin/deferments")
    assert deferments.status_code == 404
    assert "Deferments is switched off on this deployment" in deferments.text

    client.cookies.delete(AUTH_COOKIE_NAME)
    anon = client.get("/grouping")
    assert anon.status_code == 404
    assert anon.headers["content-type"].startswith("text/html")
    assert "Grouping is switched off on this deployment" in anon.text
    # No user → no nav links (the .sidebar-nav CSS rule itself may ship).
    assert 'href="/admin"' not in anon.text


@pytest.mark.asyncio
async def test_flag_off_blocks_pages_before_auth(client: TestClient, monkeypatch):
    """The gate runs before the login redirect: anonymous visitors also
    get 404, not a redirect that would confirm the route exists."""
    _set_flags(monkeypatch, deferments=False, grouping=False)

    response = client.get("/grouping", follow_redirects=False)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_flag_off_blocks_api_even_for_super_admin(
    client: TestClient, monkeypatch
):
    """API endpoints of flag-off features 404 for super-admin params,
    across read and write verbs."""
    _set_flags(monkeypatch, deferments=False, grouping=False)

    any_id = str(uuid.uuid4())
    requests = [
        ("GET", "/api/v1/deferments"),
        ("DELETE", f"/api/v1/deferments/{any_id}"),
        ("GET", "/api/v1/groupings/"),
        ("DELETE", f"/api/v1/groupings/{any_id}"),
    ]
    for method, url in requests:
        response = client.request(method, url, params=SUPER_ADMIN_PARAMS)
        assert response.status_code == 404, f"{method} {url}"
        assert response.headers["content-type"].startswith("application/json")
        assert "not available on this deployment" in response.json()["detail"]


# --- Flags off: unreachable for every role, including super admins ---


def _set_discussions_flag(monkeypatch, enabled: bool) -> None:
    """Point the discussions flag at a fixed posture (both Settings copies)."""
    for settings_obj in {get_settings(), main_app.state.settings}:
        monkeypatch.setattr(settings_obj, "FEATURE_DISCUSSIONS", enabled)


@pytest.mark.asyncio
async def test_discussions_flag_off_hides_nav_and_entry_points(
    client: TestClient,
    db_session: AsyncSession,
    monkeypatch,
):
    """A signed-in super admin sees no trace of the board: sidebar entry,
    list page and post-detail URLs all 404."""
    _set_discussions_flag(monkeypatch, False)
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert 'href="/admin/discussions"' not in dashboard.text

    assert client.get("/admin/discussions").status_code == 404
    assert client.get("/admin/discussions/posts/some-id").status_code == 404


@pytest.mark.asyncio
async def test_discussions_flag_off_blocks_api_even_for_super_admin(
    client: TestClient,
    db_session: AsyncSession,
    monkeypatch,
):
    """Board API routes 404 across verbs for a signed-in super admin, and
    for anonymous callers too — the gate runs before auth."""
    _set_discussions_flag(monkeypatch, False)
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    any_id = str(uuid.uuid4())
    requests = [
        ("GET", "/api/v1/discussions/posts"),
        ("POST", "/api/v1/discussions/posts"),
        ("GET", f"/api/v1/discussions/posts/{any_id}"),
        ("PATCH", f"/api/v1/discussions/posts/{any_id}"),
        ("DELETE", f"/api/v1/discussions/posts/{any_id}"),
        ("POST", f"/api/v1/discussions/posts/{any_id}/comments"),
        ("PATCH", f"/api/v1/discussions/comments/{any_id}"),
    ]
    for method, url in requests:
        response = client.request(method, url, json={"title": "x", "body": "y", "category": "requests"})
        assert response.status_code == 404, f"{method} {url}"
        assert response.headers["content-type"].startswith("application/json")
        assert "not available on this deployment" in response.json()["detail"]

    client.cookies.delete(AUTH_COOKIE_NAME)
    assert client.get("/api/v1/discussions/posts").status_code == 404


@pytest.mark.asyncio
async def test_discussions_flag_on_restores_routes_and_nav(
    client: TestClient,
    db_session: AsyncSession,
):
    """With the flag on (the conftest default posture), the board page and
    nav entry are reachable for an admin."""
    admin = User(
        email="board-admin@example.com", name="Board Admin", status="active", role="admin"
    )
    db_session.add(admin)
    await db_session.commit()
    await _sign_in(client, db_session, admin)

    dashboard = client.get("/admin")
    assert 'href="/admin/discussions"' in dashboard.text
    assert client.get("/admin/discussions").status_code == 200


# --- Flags on: the dev posture ---


@pytest.mark.asyncio
async def test_flag_on_restores_routes_and_nav(
    client: TestClient,
    db_session: AsyncSession,
    sample_grouping,
):
    """With flags on (the conftest default), pages, API, and nav entries
    are all reachable again for a super admin."""
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert 'href="/admin/deferments"' in dashboard.text
    assert 'href="/grouping"' in dashboard.text

    assert client.get("/grouping").status_code == 200
    assert client.get(
        "/api/v1/deferments", params=SUPER_ADMIN_PARAMS
    ).status_code == 200
    assert client.get(
        "/api/v1/groupings/", params=SUPER_ADMIN_PARAMS
    ).status_code == 200


@pytest.mark.asyncio
async def test_flags_gate_independently(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """Each flag controls only its own feature: Deferments off while
    Grouping is on hides one and keeps the other reachable."""
    _set_flags(monkeypatch, deferments=False, grouping=True)

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    dashboard = client.get("/admin")
    assert 'href="/admin/deferments"' not in dashboard.text
    assert 'href="/grouping"' in dashboard.text

    assert client.get(
        "/api/v1/deferments", params=SUPER_ADMIN_PARAMS
    ).status_code == 404
    assert client.get(
        "/api/v1/groupings/", params=SUPER_ADMIN_PARAMS
    ).status_code == 200
