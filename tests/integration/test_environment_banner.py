"""Environment banner (dev-environment identifier strip).

When ENVIRONMENT_BANNER is set (Railway development), every page —
including pre-auth standalone pages like the login screen — carries a
thin fixed strip at the very top naming the environment, so users never
have to read the URL to know where they are. Unset (production, local
dev default): no markup, no body class, no layout change at all.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.session import create_user_session
from parade_state.config import Settings, get_settings
from parade_state.main import app as main_app
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


def _set_banner(monkeypatch, text: str) -> None:
    """Point ENVIRONMENT_BANNER at a fixed value for one test.

    Patches both live Settings objects — templates read the module-level
    app's ``app.state.settings`` snapshot, which diverges from the cached
    ``get_settings()`` instance once test_production_hardening clears the
    settings cache (same pattern as the feature-flag tests).
    """
    for settings_obj in {get_settings(), main_app.state.settings}:
        monkeypatch.setattr(settings_obj, "ENVIRONMENT_BANNER", text)


def test_banner_defaults_off(monkeypatch):
    """With no env var set (prod posture) the banner is empty."""
    monkeypatch.delenv("ENVIRONMENT_BANNER", raising=False)

    settings = Settings()

    assert settings.ENVIRONMENT_BANNER == ""


@pytest.mark.asyncio
async def test_no_banner_markup_when_unset(
    client: TestClient, db_session: AsyncSession, sample_users
):
    """Unset means literally nothing: no strip, no body class — on both
    the standalone login page and an authenticated app page. (The static
    CSS rule may ship; nothing matches it, so no layout effect.)"""
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert '<div class="env-banner"' not in response.text
    assert 'class="env-banner"' not in response.text

    await _sign_in(client, db_session, sample_users["admin"])
    app_page = client.get("/admin")
    assert app_page.status_code == 200
    assert '<div class="env-banner"' not in app_page.text
    assert 'class="env-banner"' not in app_page.text


@pytest.mark.asyncio
async def test_banner_shown_pre_auth_on_login_page(client: TestClient, monkeypatch):
    """The login screen is where environment confusion happens, so the
    banner must render there — before any authentication."""
    _set_banner(monkeypatch, "Development environment")

    response = client.get("/auth/login")

    assert response.status_code == 200
    assert '<body class="env-banner">' in response.text
    assert '<div class="env-banner"' in response.text
    assert "Development environment" in response.text


@pytest.mark.asyncio
async def test_banner_shown_on_app_and_no_access_pages(
    client: TestClient, db_session: AsyncSession, sample_users, monkeypatch
):
    """Authenticated app pages (base.html shell) and the standalone
    no-access page both carry the banner."""
    _set_banner(monkeypatch, "Development environment")
    await _sign_in(client, db_session, sample_users["admin"])

    app_page = client.get("/admin")
    assert app_page.status_code == 200
    assert '<body class="env-banner">' in app_page.text

    no_access = client.get("/auth/no-access")
    assert no_access.status_code == 403
    assert '<body class="env-banner">' in no_access.text
    assert "Development environment" in no_access.text


@pytest.mark.asyncio
async def test_banner_text_is_escaped(client: TestClient, monkeypatch):
    """The banner text comes from an env var into templates that do not
    autoescape; the explicit escape filter must neutralize markup."""
    _set_banner(monkeypatch, "Dev <script>alert(1)</script>")

    response = client.get("/auth/login")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
