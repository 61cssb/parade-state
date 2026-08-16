"""Settings behavior: environment detection and production fail-fast.

Settings reads the process environment at instantiation, so every test
builds a fresh instance under a monkeypatched environment instead of the
cached get_settings().
"""

import pytest

from parade_state.config import REQUIRED_IN_PRODUCTION, Settings

PRODUCTION_ENV = {
    "SESSION_SECRET": "unit-test-secret-at-least-32-chars",
    "GOOGLE_CLIENT_ID": "unit-test-client-id",
    "GOOGLE_CLIENT_SECRET": "unit-test-client-secret",
    "SUPER_ADMIN_EMAIL": "admin@example.com",
    "ALLOWED_ORIGINS": "https://parade.example.com",
}


@pytest.fixture
def production_env(monkeypatch):
    """Apply a complete, valid production environment."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    for key, value in PRODUCTION_ENV.items():
        monkeypatch.setenv(key, value)
    return PRODUCTION_ENV


def test_validate_production_names_every_missing_variable(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://parade.example.com")
    for name in REQUIRED_IN_PRODUCTION:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        Settings().validate()

    for name in REQUIRED_IN_PRODUCTION:
        assert name in str(excinfo.value)


def test_validate_production_rejects_wildcard_origins(production_env, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")

    with pytest.raises(RuntimeError) as excinfo:
        Settings().validate()

    assert "ALLOWED_ORIGINS" in str(excinfo.value)


def test_validate_production_accepts_complete_configuration(production_env):
    Settings().validate()  # must not raise


def test_validate_development_tolerates_missing_settings(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    for name in REQUIRED_IN_PRODUCTION:
        monkeypatch.delenv(name, raising=False)

    Settings().validate()  # must not raise


def test_railway_is_detected_as_production(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "some-project")

    assert Settings().is_production


def test_railway_service_id_also_detected(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "some-service")

    assert Settings().is_production


def test_environment_matching_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "Production")

    assert Settings().is_production


def test_no_production_markers_means_development(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAILWAY_SERVICE_ID", raising=False)

    assert not Settings().is_production


def test_auth_cookie_secure_defaults_to_environment(production_env, monkeypatch):
    assert Settings().AUTH_COOKIE_SECURE is True

    monkeypatch.setenv("ENVIRONMENT", "development")
    assert Settings().AUTH_COOKIE_SECURE is False


def test_auth_cookie_secure_can_be_overridden(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    assert Settings().AUTH_COOKIE_SECURE is True

    # Escape hatch for local HTTP testing of the production configuration
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    assert Settings().AUTH_COOKIE_SECURE is False


def test_session_secret_has_no_fallback_value(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    assert Settings().SESSION_SECRET == ""
