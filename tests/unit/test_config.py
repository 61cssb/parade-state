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


def test_feature_ippt_defaults_off(monkeypatch):
    monkeypatch.delenv("FEATURE_IPPT", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert Settings().FEATURE_IPPT is False


def test_feature_ippt_enables_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("FEATURE_IPPT", "true")
    assert Settings().FEATURE_IPPT is True


def test_feature_ippt_force_disabled_in_production(production_env, monkeypatch, caplog):
    # Local-testing-only feature (until further notice): a production
    # deployment — including both Railway environments, which detect as
    # production — can never enable it, no matter what the env var says.
    monkeypatch.setenv("FEATURE_IPPT", "true")
    with caplog.at_level("WARNING"):
        settings = Settings()
    assert settings.FEATURE_IPPT is False
    assert any("FEATURE_IPPT" in record.message for record in caplog.records)


def test_feature_ippt_force_disabled_when_railway_detected(monkeypatch):
    # No explicit ENVIRONMENT, but Railway's injected ids mean production.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "proj")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "svc")
    monkeypatch.setenv("FEATURE_IPPT", "true")
    assert Settings().FEATURE_IPPT is False


def test_other_feature_flags_unaffected_by_ippt_guard(production_env, monkeypatch):
    # The force-off is IPPT-specific; other flags keep their semantics.
    monkeypatch.setenv("FEATURE_DEFERMENTS", "true")
    settings = Settings()
    assert settings.FEATURE_DEFERMENTS is True
    assert settings.FEATURE_IPPT is False
