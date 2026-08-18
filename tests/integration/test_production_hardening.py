"""Production hardening behavior of the application factory.

Covers the acceptance criteria from issues/13-urgent-prod-hardening.md:
boot refusal with missing secrets, CORS restricted to ALLOWED_ORIGINS,
OpenAPI docs gated off in production, and Secure auth cookies.
"""

import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from parade_state.config import REQUIRED_IN_PRODUCTION, Settings, get_settings
from parade_state.main import create_app
from parade_state.utils import cookies

REPO_ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_ENV = {
    "SESSION_SECRET": "integration-test-secret-at-least-32-chars",
    "GOOGLE_CLIENT_ID": "integration-test-client-id",
    "GOOGLE_CLIENT_SECRET": "integration-test-client-secret",
    "SUPER_ADMIN_EMAIL": "admin@example.com",
    "ALLOWED_ORIGINS": "https://parade.example.com",
}


def _production_settings(monkeypatch) -> Settings:
    """Build settings from a complete, valid production environment."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    for key, value in PRODUCTION_ENV.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_app_refuses_to_boot_in_production_without_secrets(tmp_path):
    """Importing the app with production env and missing secrets must fail,
    naming every missing variable, instead of booting insecurely."""
    env = {
        **os.environ,
        "ENVIRONMENT": "production",
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    for name in (*REQUIRED_IN_PRODUCTION, "ALLOWED_ORIGINS"):
        env.pop(name, None)

    result = subprocess.run(
        [sys.executable, "-c", "import parade_state.main"],
        # Run outside the repo so the developer's .env is not picked up
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode != 0
    for name in REQUIRED_IN_PRODUCTION:
        assert name in result.stderr
    assert "ALLOWED_ORIGINS" in result.stderr


def test_cors_allows_only_configured_origins(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://admin.example.com")

    client = TestClient(create_app(Settings()))

    allowed = client.get("/health", headers={"Origin": "https://admin.example.com"})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://admin.example.com"
    assert allowed.headers["access-control-allow-credentials"] == "true"

    denied = client.get("/health", headers={"Origin": "https://evil.example"})
    assert denied.status_code == 200
    assert "access-control-allow-origin" not in denied.headers


def test_openapi_docs_disabled_in_production(monkeypatch):
    _production_settings(monkeypatch)
    client = TestClient(create_app(Settings()))

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_openapi_docs_available_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")

    client = TestClient(create_app(Settings()))

    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_root_redirects_to_login(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")

    response = TestClient(create_app(Settings())).get("/", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_oauth_state_cookie_carries_secure_in_production(monkeypatch):
    """The OAuth-start response sets the short-lived session_data cookie."""
    _production_settings(monkeypatch)

    client = TestClient(create_app(Settings()), base_url="https://testserver")
    response = client.get("/auth/oauth/start", follow_redirects=False)

    assert response.status_code == 302
    header = response.headers["set-cookie"].lower()
    assert "session_data=" in header
    assert "secure" in header
    assert "httponly" in header
    assert "samesite=lax" in header


def test_auth_cookie_carries_secure_in_production(monkeypatch):
    _production_settings(monkeypatch)
    get_settings.cache_clear()
    try:
        response = RedirectResponse(url="/admin")
        cookies.set_auth_cookie(response, "session-token")

        header = response.headers["set-cookie"]
        assert "Secure" in header
        assert "HttpOnly" in header
        assert "SameSite=lax" in header
    finally:
        get_settings.cache_clear()


def test_auth_cookie_not_secure_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    get_settings.cache_clear()
    try:
        response = RedirectResponse(url="/admin")
        cookies.set_auth_cookie(response, "session-token")

        assert "Secure" not in response.headers["set-cookie"]
    finally:
        get_settings.cache_clear()
