"""Configuration management utilities.

Settings are read from the environment each time a ``Settings`` instance is
created; :func:`get_settings` caches one instance per process. Production
deployments must call :meth:`Settings.validate` (the application factory
does) so misconfiguration fails at boot instead of silently running with
known-to-the-world secrets.
"""

from functools import lru_cache

from parade_state.utils import env

DEVELOPMENT = "development"
PRODUCTION = "production"

#: Variables that must be set (non-empty) when running in production.
REQUIRED_IN_PRODUCTION = (
    "SESSION_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "SUPER_ADMIN_EMAIL",
)


def _detect_environment() -> str:
    """Return the runtime environment name.

    An explicit ``ENVIRONMENT`` (development|production, case-insensitive)
    always wins. Otherwise Railway deployments — where the platform injects
    ``RAILWAY_PROJECT_ID`` / ``RAILWAY_SERVICE_ID`` — are treated as
    production so the hardening below cannot be silently skipped by
    forgetting one variable.
    """
    explicit = (env.get("ENVIRONMENT") or "").strip().lower()
    if explicit:
        return explicit
    if env.is_set("RAILWAY_PROJECT_ID") or env.is_set("RAILWAY_SERVICE_ID"):
        return PRODUCTION
    return DEVELOPMENT


class Settings:
    """Application settings read from the environment at instantiation."""

    def __init__(self) -> None:
        # Application
        self.APP_NAME: str = "Parade State Management System"
        self.APP_VERSION: str = "0.1.0"
        self.DEBUG: bool = env.get_bool("DEBUG", default=False)
        self.ENVIRONMENT: str = _detect_environment()

        # Database
        self.DATABASE_URL: str = (
            env.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
            or "sqlite+aiosqlite:///:memory:"
        )

        # Authentication. No fallback secrets on purpose: production must
        # fail fast (see validate()); development gets a random per-process
        # session secret from the application factory instead of a
        # known-to-the-world constant.
        self.GOOGLE_CLIENT_ID: str = env.get("GOOGLE_CLIENT_ID", "") or ""
        self.GOOGLE_CLIENT_SECRET: str = env.get("GOOGLE_CLIENT_SECRET", "") or ""
        self.SESSION_SECRET: str = env.get("SESSION_SECRET", "") or ""
        self.SUPER_ADMIN_EMAIL: str = env.get("SUPER_ADMIN_EMAIL", "") or ""

        # Auth cookies: Secure (HTTPS-only) by default in production.
        # AUTH_COOKIE_SECURE=false is an escape hatch for local HTTP testing.
        self.AUTH_COOKIE_SECURE: bool = env.get_bool(
            "AUTH_COOKIE_SECURE", default=self.is_production
        )

        # CORS: the only origins allowed to make credentialed requests.
        # Production must list explicit origins (validate() rejects "*").
        self.ALLOWED_ORIGINS: list[str] = env.get_list(
            "ALLOWED_ORIGINS", separator=",", default=["*"]
        )

        # Application URLs
        self.APP_BASE_URL: str = (
            env.get("APP_BASE_URL", "http://localhost:8000") or "http://localhost:8000"
        )

        # Super-admin database restore from the admin UI. Kill switch for
        # operators who prefer the CLI path only.
        self.RESTORE_ENABLED: bool = env.get_bool("RESTORE_ENABLED", default=True)

        # Testing-only: super-admin purge of all nominal rolls and downstream
        # data from the admin UI. Off in production unless explicitly enabled.
        self.PURGE_ENABLED: bool = env.get_bool(
            "PURGE_ENABLED", default=not self.is_production
        )

        # Feature flags: features that are not ready for a deployment are
        # hidden entirely — no nav entry, page and API routes unreachable —
        # for every role including super admins. Default off; the
        # development environment opts in via env vars. Toggling is an
        # env-var change plus service restart (see parade_state.features).
        self.FEATURE_DEFERMENTS: bool = env.get_bool(
            "FEATURE_DEFERMENTS", default=False
        )
        self.FEATURE_GROUPING: bool = env.get_bool("FEATURE_GROUPING", default=False)
        self.FEATURE_STRENGTH: bool = env.get_bool("FEATURE_STRENGTH", default=False)
        self.FEATURE_DISCUSSIONS: bool = env.get_bool(
            "FEATURE_DISCUSSIONS", default=False
        )

        # Kill switches for the two shipped core features (issue 23): the
        # inverse of the flags above — default ON, so a missing env var can
        # never hide a shipped feature. Explicit `false` takes the feature
        # offline entirely (same gating mechanics) without a deploy, e.g.
        # when a data-corrupting bug appears mid-window.
        self.FEATURE_NOMINALROLL: bool = env.get_bool(
            "FEATURE_NOMINALROLL", default=True
        )
        self.FEATURE_ATTENDANCE: bool = env.get_bool(
            "FEATURE_ATTENDANCE", default=True
        )

        # Non-production identifier banner: when set (e.g. on the Railway
        # development environment), a thin fixed strip at the very top of
        # every page shows this text, so users never have to read the URL
        # to know which environment they are on. Unset in production —
        # no markup, no CSS, no layout change at all.
        self.ENVIRONMENT_BANNER: str = env.get("ENVIRONMENT_BANNER", "") or ""

    @property
    def is_production(self) -> bool:
        """Whether the app runs in the production environment."""
        return self.ENVIRONMENT == PRODUCTION

    def validate(self) -> None:
        """Fail fast on unsafe production configuration.

        Raises:
            RuntimeError: Naming every missing or unsafe production setting,
                so the process refuses to boot rather than silently running
                with wildcard CORS or known-to-the-world secrets.
        """
        if not self.is_production:
            return

        problems = [
            f"{name} is not set"
            for name in REQUIRED_IN_PRODUCTION
            if not getattr(self, name)
        ]
        if "*" in self.ALLOWED_ORIGINS:
            problems.append(
                'ALLOWED_ORIGINS is "*" — list explicit origins in production'
            )
        if problems:
            raise RuntimeError(
                "Refusing to start in production. Fix these settings:\n"
                + "\n".join(f"  - {problem}" for problem in problems)
            )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
