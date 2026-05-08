"""Configuration management utilities."""

import os
from functools import lru_cache
from typing import List


class Settings:
    """Application settings."""

    # Application
    APP_NAME: str = "Parade State Management System"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite+aiosqlite:///:memory:",
    )

    # Authentication
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "dev-secret-change-in-production")
    SUPER_ADMIN_EMAIL: str = os.getenv("SUPER_ADMIN_EMAIL", "")

    # CORS
    ALLOWED_ORIGINS: List[str] = os.getenv("ALLOWED_ORIGINS", "*").split(",")

    # Application URLs
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000")


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()