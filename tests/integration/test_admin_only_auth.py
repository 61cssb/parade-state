"""Tests for the admin-only authentication policy (issue 12).

The system is admin-only: only active admins (admin / super_admin) get a
session. Unknown Google sign-ins are auto-registered as `unrecognised`
and see the no-access page. The viewer role is deferred, so viewer-facing
routes are gated on admin role.

The OAuth exchange is faked by monkeypatching `get_oauth` in
`parade_state.web.auth` — the callback then runs against the test DB via
the client fixture's `get_db_session` override.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.session import create_user_session
from parade_state.models import User, UserSession
from parade_state.utils.cookies import AUTH_COOKIE_NAME


class _FakeGoogleClient:
    """Stands in for the authlib client; hands back fixed userinfo."""

    def __init__(self, userinfo: dict):
        self._userinfo = userinfo

    async def authorize_access_token(self, request):
        return {"userinfo": self._userinfo}


class _FakeOAuth:
    def __init__(self, userinfo: dict):
        self._userinfo = userinfo

    def create_client(self, name: str):
        return _FakeGoogleClient(self._userinfo)


def _mock_sign_in(monkeypatch, email: str, name: str = "Test User") -> None:
    """Make GET /auth/callback behave as a completed Google sign-in."""
    from parade_state.web import auth as web_auth

    monkeypatch.setattr(
        web_auth, "get_oauth", lambda: _FakeOAuth({"email": email, "name": name})
    )


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _count_sessions(db: AsyncSession) -> int:
    result = await db.execute(select(UserSession))
    return len(result.scalars().all())


@pytest.mark.asyncio
async def test_unknown_sign_in_registers_unrecognised_and_gets_no_access(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """A Google account not in the users table is auto-registered as
    unrecognised, sees the no-access page, and receives no session."""
    _mock_sign_in(monkeypatch, "stranger@example.com")

    response = client.get("/auth/callback")

    assert response.status_code == 403
    assert "No Access" in response.text
    assert "stranger@example.com" in response.text

    user = await _get_user_by_email(db_session, "stranger@example.com")
    assert user is not None
    assert user.status == "unrecognised"
    assert user.role == "user"

    # No session was issued — the account cannot reach any page.
    assert await _count_sessions(db_session) == 0


@pytest.mark.asyncio
async def test_non_admin_active_user_sign_in_gets_no_access(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """An active but non-admin user gets the no-access page, not /grouping."""
    db_session.add(
        User(
            email="viewer@example.com",
            name="Viewer",
            status="active",
            role="user",
        )
    )
    await db_session.commit()

    _mock_sign_in(monkeypatch, "viewer@example.com")

    response = client.get("/auth/callback")

    assert response.status_code == 403
    assert "No Access" in response.text
    assert await _count_sessions(db_session) == 0


@pytest.mark.asyncio
async def test_suspended_user_sign_in_returns_403_not_500(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """The suspended-account 403 must not be swallowed into a 500."""
    db_session.add(
        User(
            email="suspended@example.com",
            name="Suspended",
            status="suspended",
            role="admin",
        )
    )
    await db_session.commit()

    _mock_sign_in(monkeypatch, "suspended@example.com")

    response = client.get("/auth/callback")

    assert response.status_code == 403
    assert response.json()["detail"] == "Account suspended"


@pytest.mark.asyncio
async def test_super_admin_bootstrap_sign_in_lands_on_admin(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """SUPER_ADMIN_EMAIL sign-in bootstraps an active super-admin session."""
    monkeypatch.setenv("SUPER_ADMIN_EMAIL", "bootstrap@example.com")
    _mock_sign_in(monkeypatch, "bootstrap@example.com")

    response = client.get("/auth/callback", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin")

    user = await _get_user_by_email(db_session, "bootstrap@example.com")
    assert user.status == "active"
    assert user.role == "super_admin"
    assert await _count_sessions(db_session) == 1
    assert AUTH_COOKIE_NAME in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_admin_sign_in_lands_on_admin(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """An existing active admin signs in straight to /admin."""
    db_session.add(
        User(email="admin@example.com", name="Admin", status="active", role="admin")
    )
    await db_session.commit()

    _mock_sign_in(monkeypatch, "admin@example.com")

    response = client.get("/auth/callback", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin")
    assert await _count_sessions(db_session) == 1


@pytest.mark.asyncio
async def test_promoted_unrecognised_user_can_sign_in(
    client: TestClient, db_session: AsyncSession, monkeypatch
):
    """Acceptance: super-admin promotes an unrecognised user to admin via
    the users API, and the next sign-in works normally."""
    # First sign-in: registered as unrecognised, no access.
    _mock_sign_in(monkeypatch, "newadmin@example.com")
    response = client.get("/auth/callback")
    assert response.status_code == 403

    user = await _get_user_by_email(db_session, "newadmin@example.com")
    assert user.status == "unrecognised"

    # Super-admin promotes via the API (needs a real session token).
    super_admin = User(
        email="root@example.com", name="Root", status="active", role="super_admin"
    )
    db_session.add(super_admin)
    await db_session.commit()
    sa_session = await create_user_session(
        db_session, user_id=str(super_admin.id), email=super_admin.email,
        name=super_admin.name, role=super_admin.role,
    )
    await db_session.commit()

    patch = client.patch(
        f"/api/v1/users/{user.id}",
        json={"role": "admin", "status": "active"},
        headers={"Authorization": f"Bearer {sa_session.token}"},
    )
    assert patch.status_code == 200
    assert patch.json()["role"] == "admin"
    assert patch.json()["status"] == "active"

    # Second sign-in: now an active admin.
    response = client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin")


@pytest.mark.asyncio
async def test_viewer_routes_redirect_non_admins_to_no_access(
    client: TestClient, db_session: AsyncSession
):
    """A regular user holding a (legacy) session cannot reach viewer or
    admin pages — every authenticated surface redirects or refuses."""
    regular = User(
        email="legacy@example.com", name="Legacy", status="active", role="user"
    )
    db_session.add(regular)
    await db_session.commit()
    session = await create_user_session(
        db_session, user_id=str(regular.id), email=regular.email,
        name=regular.name, role=regular.role,
    )
    await db_session.commit()

    client.cookies.set(AUTH_COOKIE_NAME, session.token)

    for path in ("/grouping", "/attendance", "/nominal-roll"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302, path
        assert response.headers["location"].endswith("/auth/no-access"), path

    # Admin pages treat the non-admin as unauthenticated — no content.
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/auth/login")

    # Login page does not send them to a viewer surface either.
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/auth/no-access")


@pytest.mark.asyncio
async def test_admin_session_reaches_grouping_and_login_redirects_to_admin(
    client: TestClient, db_session: AsyncSession
):
    """An admin with a session can still reach the (now admin-gated)
    viewer routes, and /auth/login shortcuts to /admin."""
    admin = User(
        email="admin2@example.com", name="Admin Two", status="active", role="admin"
    )
    db_session.add(admin)
    await db_session.commit()
    session = await create_user_session(
        db_session, user_id=str(admin.id), email=admin.email,
        name=admin.name, role=admin.role,
    )
    await db_session.commit()

    client.cookies.set(AUTH_COOKIE_NAME, session.token)

    response = client.get("/grouping")
    assert response.status_code == 200

    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin")


def test_no_access_page_renders_without_session(client: TestClient):
    """The standalone /auth/no-access route renders a 403 page."""
    response = client.get("/auth/no-access")
    assert response.status_code == 403
    assert "No Access" in response.text
