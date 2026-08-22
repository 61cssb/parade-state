"""FastAPI application setup and configuration."""

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

# Load environment variables from .env file
load_dotenv()

from parade_state.admin_routes import router as admin_router
from parade_state.api import (
    access_control,
    admin_purge,
    attendance,
    audit,
    auth,
    csv_upload,
    db_restore,
    deferments,
    discussions,
    groupings,
    nominal_rolls,
    personnel,
    sessions,
    tagging,
    users,
)
from parade_state.auth.admin_dependencies import get_current_admin_user_optional
from parade_state.config import Settings, get_settings
from parade_state.db import init_database
from parade_state.features import FeatureDisabledError, feature_label, require_feature
from parade_state.web.attendance import router as web_attendance_router
from parade_state.web.auth import router as web_auth_router
from parade_state.web.grouping import router as web_grouping_router
from parade_state.web.nominal_roll import router as web_nominal_roll_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Prebuilt settings; defaults to the process-wide cached
            settings. Tests inject custom configurations.

    Returns:
        Configured FastAPI application instance

    Raises:
        RuntimeError: In production, when required settings are missing —
            the process refuses to boot (see Settings.validate())
    """
    if settings is None:
        settings = get_settings()
    settings.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage application lifecycle."""
        # Startup
        # Only initialize if not already initialized (prevents test
        # database from being reset)
        from parade_state.db import get_session_maker

        if get_session_maker() is None:
            init_database(settings.DATABASE_URL)
        yield
        # Shutdown
        pass

    app = FastAPI(
        title=settings.APP_NAME,
        description="Battalion parade state management with access control",
        version=settings.APP_VERSION,
        lifespan=lifespan,
        # OpenAPI docs are a development aid; production does not expose
        # the API surface.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Set up Jinja2 templates directory
    templates_dir = Path(__file__).parent / "templates"
    app.state.templates_dir = str(templates_dir)  # Store directory path instead

    # Live settings for templates: nav entries gate on feature flags via
    # ``request.app.state.settings.FEATURE_*`` (see parade_state.features).
    app.state.settings = settings

    # CORS middleware: credentialed requests are accepted only from the
    # configured origins. Production validation rejects "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Session middleware for the OAuth flow. Production must set a real
    # SESSION_SECRET (enforced by validate()); development falls back to a
    # random per-process secret so no known-to-the-world key is ever used.
    session_secret = settings.SESSION_SECRET
    if not session_secret:
        session_secret = secrets.token_urlsafe(32)
        logger.warning(
            "SESSION_SECRET not set — using a random per-process secret; "
            "OAuth sessions will not survive a restart. Set SESSION_SECRET "
            "to silence this warning."
        )
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        max_age=86400,  # 24 hours
        session_cookie="session_data",  # Different name to avoid conflict
        same_site="lax",  # Allow same-site redirects
        https_only=settings.AUTH_COOKIE_SECURE,  # Secure flag on the OAuth-state cookie
    )

    # Include routers
    # User-facing web routes (OAuth flows, redirects)
    app.include_router(web_auth_router, prefix="/auth", tags=["web-auth"])

    # User-facing web routes (non-admin views). The grouping pages are
    # feature-flagged: flag-off means 404 for every role. The two core
    # features carry default-on kill switches (issue 23) with the same
    # flag-off behavior.
    app.include_router(
        web_grouping_router,
        tags=["web-grouping"],
        dependencies=[Depends(require_feature("FEATURE_GROUPING"))],
    )
    app.include_router(
        web_attendance_router,
        tags=["web-attendance"],
        dependencies=[Depends(require_feature("FEATURE_ATTENDANCE"))],
    )
    app.include_router(
        web_nominal_roll_router,
        tags=["web-nominal-roll"],
        dependencies=[Depends(require_feature("FEATURE_NOMINALROLL"))],
    )

    # Admin interface routes
    app.include_router(admin_router, tags=["admin"])

    # REST API endpoints (JSON responses)
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["api-auth"])
    app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
    app.include_router(
        groupings.router,
        prefix="/api/v1/groupings",
        tags=["groupings"],
        dependencies=[Depends(require_feature("FEATURE_GROUPING"))],
    )
    app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(
        attendance.router,
        prefix="/api/v1/attendance",
        tags=["attendance"],
        dependencies=[Depends(require_feature("FEATURE_ATTENDANCE"))],
    )
    app.include_router(personnel.router, prefix="/api/v1", tags=["personnel"])
    app.include_router(
        access_control.router, prefix="/api/v1/access-control", tags=["access-control"]
    )
    app.include_router(
        csv_upload.router,
        prefix="/api/v1/csv",
        tags=["csv-upload"],
        dependencies=[Depends(require_feature("FEATURE_NOMINALROLL"))],
    )
    app.include_router(
        nominal_rolls.router,
        prefix="/api/v1/nominal-rolls",
        tags=["nominal-rolls"],
        dependencies=[Depends(require_feature("FEATURE_NOMINALROLL"))],
    )
    app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
    app.include_router(
        deferments.router,
        prefix="/api/v1/deferments",
        tags=["deferments"],
        dependencies=[Depends(require_feature("FEATURE_DEFERMENTS"))],
    )
    app.include_router(
        discussions.router,
        prefix="/api/v1/discussions",
        tags=["discussions"],
        dependencies=[Depends(require_feature("FEATURE_DISCUSSIONS"))],
    )
    app.include_router(
        tagging.router,
        prefix="/api/v1/taggings",
        tags=["taggings"],
        dependencies=[Depends(require_feature("FEATURE_NOMINALROLL"))],
    )
    app.include_router(
        db_restore.router, prefix="/api/v1/admin", tags=["db-restore"]
    )
    app.include_router(
        admin_purge.router, prefix="/api/v1/admin", tags=["admin-purge"]
    )

    @app.exception_handler(FeatureDisabledError)
    async def feature_disabled_handler(
        request: Request, exc: FeatureDisabledError
    ) -> HTMLResponse | JSONResponse:
        """A flag-gated feature was reached while its flag is off.

        API routes keep the JSON 404 (detail names the env var for
        operators); page routes get a styled HTML 404 explaining the
        feature is deliberately disabled, so bookmark / saved-link users
        do not mistake it for a broken URL.
        """
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=404,
                content={
                    "detail": (
                        "This feature is not available on this deployment "
                        f"({exc.flag}=false)"
                    )
                },
            )
        user = None
        try:
            current = await get_current_admin_user_optional(request)
            if current:
                user = {
                    "id": str(current.id),
                    "name": current.name,
                    "email": current.email,
                    "role": current.role,
                }
        except Exception:  # noqa: BLE001 — the 404 page must render regardless
            user = None
        template_env = Environment(
            loader=FileSystemLoader(app.state.templates_dir),
            autoescape=False,
            cache_size=0,
        )
        html = template_env.get_template("feature_disabled.html").render(
            request=request,
            user=user,
            feature_label=feature_label(exc.flag),
        )
        return HTMLResponse(content=html, status_code=404)

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "version": settings.APP_VERSION}

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        """Send the bare domain to the login flow.

        /auth/login already routes by role: active admins continue to
        /admin, everyone else gets the no-access page.
        """
        return RedirectResponse(url="/auth/login", status_code=302)

    return app


app = create_app()
