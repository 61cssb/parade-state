"""Configuration management utilities."""

from functools import lru_cache

from parade_state.utils import env


class Settings:
    """Application settings."""

    # Application
    APP_NAME: str = "Parade State Management System"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = env.get_bool("DEBUG", default=False)

    # Database
    DATABASE_URL: str = env.get(
        "DATABASE_URL",
        "sqlite+aiosqlite:///:memory:",
    )

    # Authentication
    GOOGLE_CLIENT_ID: str = env.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = env.get("GOOGLE_CLIENT_SECRET", "")
    SESSION_SECRET: str = env.get("SESSION_SECRET", "dev-secret-change-in-production")
    SUPER_ADMIN_EMAIL: str = env.get("SUPER_ADMIN_EMAIL", "")

    # CORS
    ALLOWED_ORIGINS: list[str] = env.get_list(
        "ALLOWED_ORIGINS", separator=",", default=["*"]
    )

    # Application URLs
    APP_BASE_URL: str = env.get("APP_BASE_URL", "http://localhost:8000")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
