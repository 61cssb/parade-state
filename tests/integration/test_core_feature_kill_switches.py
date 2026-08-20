"""Core-feature kill switches (issue 23).

FEATURE_NOMINALROLL and FEATURE_ATTENDANCE are emergency off-switches for
the two shipped core features — the inverse of the issue-18 preview flags:
they default ON, so a missing env var can never hide a shipped feature.
Explicit ``false`` must hide the feature entirely (no nav entry, page and
API routes unreachable) for every role including super admins, so a
data-corrupting bug can be taken offline mid-window without a deploy.

The suite-wide default (conftest autouse fixture) needs no override for
these flags — default ON *is* the everywhere posture; these tests flip
the switches to False to pin the emergency posture, and pin the unset
posture as byte-identical to today's behavior.
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

# list_attendance requires the NR and date query params; the NR need not
# exist (the endpoint then just returns an empty list).
ATTENDANCE_LIST_PARAMS = {"nominal_roll_id": str(uuid.uuid4()), "date": "2026-08-20"}

NR_NAV_HREFS = ('href="/admin/csv-upload"', 'href="/nominal-roll"', 'href="/admin/taggings"')
ATTENDANCE_NAV_HREFS = ('href="/attendance"',)


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


def _set_switches(monkeypatch, nominal_roll: bool, attendance: bool) -> None:
    """Point both kill switches at a fixed posture for one test.

    Patches both live Settings objects — the cached instance route
    dependencies read, and the module-level app's ``app.state.settings``
    snapshot the nav templates read — because they diverge once
    test_production_hardening clears the settings cache.
    """
    for settings_obj in {get_settings(), main_app.state.settings}:
        monkeypatch.setattr(settings_obj, "FEATURE_NOMINALROLL", nominal_roll)
        monkeypatch.setattr(settings_obj, "FEATURE_ATTENDANCE", attendance)


# --- Configuration defaults: ON unless explicitly killed ---


def test_flags_default_on(monkeypatch):
    """With no env vars set (local dev, any misconfigured environment)
    both core features are fully available — unset can never hide them."""
    monkeypatch.delenv("FEATURE_NOMINALROLL", raising=False)
    monkeypatch.delenv("FEATURE_ATTENDANCE", raising=False)

    settings = Settings()

    assert settings.FEATURE_NOMINALROLL is True
    assert settings.FEATURE_ATTENDANCE is True


def test_flags_disabled_via_explicit_false(monkeypatch):
    """Only an explicit false kills a core feature; true keeps it on."""
    monkeypatch.setenv("FEATURE_NOMINALROLL", "false")
    monkeypatch.setenv("FEATURE_ATTENDANCE", "false")

    settings = Settings()

    assert settings.FEATURE_NOMINALROLL is False
    assert settings.FEATURE_ATTENDANCE is False

    monkeypatch.setenv("FEATURE_NOMINALROLL", "true")
    monkeypatch.setenv("FEATURE_ATTENDANCE", "true")

    settings = Settings()

    assert settings.FEATURE_NOMINALROLL is True
    assert settings.FEATURE_ATTENDANCE is True


# --- Switch off: unreachable for every role, including super admins ---


@pytest.mark.asyncio
async def test_switch_off_hides_nav(
    client: TestClient,
    db_session: AsyncSession,
    monkeypatch,
):
    """A signed-in super admin sees no trace of either feature: the
    Attendance entry and the whole Nominal Roll stack (Upload NR, Nominal
    Roll, Taggings) are gone from the sidebar."""
    _set_switches(monkeypatch, nominal_roll=False, attendance=False)
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    for href in NR_NAV_HREFS + ATTENDANCE_NAV_HREFS:
        assert href not in dashboard.text, href


@pytest.mark.asyncio
async def test_switch_off_blocks_pages_even_for_super_admin(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """Direct URLs to switch-off pages 404 for a super admin (and
    therefore for every weaker role too — the gate never consults the
    role)."""
    _set_switches(monkeypatch, nominal_roll=False, attendance=False)

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    for path in (
        "/attendance",
        "/nominal-roll",
        "/admin/csv-upload",
        "/admin/taggings",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404, path


@pytest.mark.asyncio
async def test_switch_off_page_renders_html_disabled_page(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """Page routes answer with a styled HTML 404 saying the feature is
    deliberately switched off — so bookmark/saved-link users know it is
    not a broken URL. Signed-in users keep the app shell; anonymous
    visitors get the same page without the shell."""
    _set_switches(monkeypatch, nominal_roll=False, attendance=False)
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    nr_page = client.get("/nominal-roll")
    assert nr_page.status_code == 404
    assert nr_page.headers["content-type"].startswith("text/html")
    body = nr_page.text
    assert "Nominal Roll is switched off on this deployment" in body
    assert "Your link is fine" in body
    assert "sidebar-nav" in body  # app shell renders around the message

    attendance_page = client.get("/attendance")
    assert attendance_page.status_code == 404
    assert "Attendance is switched off on this deployment" in attendance_page.text

    client.cookies.delete(AUTH_COOKIE_NAME)
    anon = client.get("/attendance")
    assert anon.status_code == 404
    assert anon.headers["content-type"].startswith("text/html")
    assert "Attendance is switched off on this deployment" in anon.text
    # No user → no nav links (the .sidebar-nav CSS rule itself may ship).
    assert 'href="/admin"' not in anon.text


@pytest.mark.asyncio
async def test_switch_off_blocks_pages_before_auth(client: TestClient, monkeypatch):
    """The gate runs before the login redirect: anonymous visitors also
    get 404, not a redirect that would confirm the route exists."""
    _set_switches(monkeypatch, nominal_roll=False, attendance=False)

    response = client.get("/attendance", follow_redirects=False)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_switch_off_blocks_api_even_for_super_admin(
    client: TestClient, monkeypatch
):
    """API endpoints of switch-off features 404 for super-admin params,
    across read and write verbs, before any body validation."""
    _set_switches(monkeypatch, nominal_roll=False, attendance=False)

    any_id = str(uuid.uuid4())
    requests = [
        ("GET", "/api/v1/attendance/"),
        ("PUT", "/api/v1/attendance/upsert"),
        ("GET", "/api/v1/nominal-rolls"),
        ("POST", f"/api/v1/nominal-rolls/{any_id}/activate-attendance"),
        ("GET", "/api/v1/taggings"),
        ("POST", "/api/v1/taggings"),
        ("GET", "/api/v1/csv/uploads"),
        ("POST", "/api/v1/csv/upload"),
    ]
    for method, url in requests:
        response = client.request(method, url, params=SUPER_ADMIN_PARAMS)
        assert response.status_code == 404, f"{method} {url}"
        assert response.headers["content-type"].startswith("application/json")
        assert "not available on this deployment" in response.json()["detail"]


# --- Switches gate independently ---


@pytest.mark.asyncio
async def test_switches_gate_independently(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """Each switch controls only its own feature: killing Nominal Roll
    leaves Attendance reachable, and vice versa."""
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    # NR off, Attendance on.
    _set_switches(monkeypatch, nominal_roll=False, attendance=True)
    dashboard = client.get("/admin")
    for href in NR_NAV_HREFS:
        assert href not in dashboard.text, href
    for href in ATTENDANCE_NAV_HREFS:
        assert href in dashboard.text, href
    assert client.get("/nominal-roll").status_code == 404
    assert client.get("/attendance").status_code == 200
    assert client.get(
        "/api/v1/nominal-rolls", params=SUPER_ADMIN_PARAMS
    ).status_code == 404
    assert client.get(
        "/api/v1/attendance/", params=ATTENDANCE_LIST_PARAMS
    ).status_code == 200

    # NR on, Attendance off.
    _set_switches(monkeypatch, nominal_roll=True, attendance=False)
    dashboard = client.get("/admin")
    for href in NR_NAV_HREFS:
        assert href in dashboard.text, href
    for href in ATTENDANCE_NAV_HREFS:
        assert href not in dashboard.text, href
    assert client.get("/nominal-roll").status_code == 200
    assert client.get("/attendance").status_code == 404
    assert client.get(
        "/api/v1/nominal-rolls", params=SUPER_ADMIN_PARAMS
    ).status_code == 200
    assert client.get(
        "/api/v1/attendance/", params=ATTENDANCE_LIST_PARAMS
    ).status_code == 404


# --- Unset (the everywhere posture): identical to today ---


@pytest.mark.asyncio
async def test_default_posture_unchanged(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """With both flags on — the posture of local dev (vars unset) and of
    Railway (vars set to true) — pages, API, and nav entries are all
    reachable exactly as before the kill switches existed. Flags are
    pinned explicitly so an ambient env var cannot skew the result; the
    unset-means-on default itself is pinned by test_flags_default_on."""
    monkeypatch.delenv("FEATURE_NOMINALROLL", raising=False)
    monkeypatch.delenv("FEATURE_ATTENDANCE", raising=False)
    for settings_obj in {get_settings(), main_app.state.settings}:
        monkeypatch.setattr(settings_obj, "FEATURE_NOMINALROLL", True)
        monkeypatch.setattr(settings_obj, "FEATURE_ATTENDANCE", True)

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    for href in NR_NAV_HREFS + ATTENDANCE_NAV_HREFS:
        assert href in dashboard.text, href

    for path in (
        "/attendance",  # renders the no-active-NR message, still 200
        "/nominal-roll",
        "/admin/csv-upload",
        "/admin/taggings",
    ):
        assert client.get(path).status_code == 200, path

    assert client.get(
        "/api/v1/nominal-rolls", params=SUPER_ADMIN_PARAMS
    ).status_code == 200
    assert client.get(
        "/api/v1/attendance/", params=ATTENDANCE_LIST_PARAMS
    ).status_code == 200
    assert client.get(
        "/api/v1/taggings", params=SUPER_ADMIN_PARAMS
    ).status_code == 200
    assert client.get(
        "/api/v1/csv/uploads", params=SUPER_ADMIN_PARAMS
    ).status_code == 200
