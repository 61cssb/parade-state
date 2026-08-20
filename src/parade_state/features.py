"""Env-var feature flags.

Feature flags hide features that are not ready for a deployment (e.g.
Deferments, Grouping) — not just from the navigation, but entirely: page
routes and API routes return 404 for every role, including super admins.
The gate sits above role checks; see issues/18-urgent-feature-flags.md.

Flags live on :class:`parade_state.config.Settings` as ``FEATURE_<NAME>``
booleans, default off, and are enabled per environment via env vars
(dev and prod are separate Railway environments with separate env vars).
Toggling a flag is an env-var change plus a service restart.

Adding a new flag is a one-line ``Settings`` change plus gating:

- routes: ``APIRouter(dependencies=[Depends(require_feature("FEATURE_X"))])``
  or ``include_router(..., dependencies=[...])`` / route-decorator
  ``dependencies=[...]`` for a single route
- nav/templates: ``{% if request.app.state.settings.FEATURE_X %}`` (the
  application factory stores the live ``Settings`` instance on
  ``app.state`` for exactly this)
"""

from fastapi import HTTPException, status

from parade_state.config import get_settings


def feature_enabled(flag: str) -> bool:
    """Whether the ``Settings`` attribute named ``flag`` is enabled.

    Reads settings at call time (per request / per render) so tests can
    toggle flags by patching the cached settings instance.
    """
    return bool(getattr(get_settings(), flag))


def require_feature(flag: str):
    """Build a FastAPI dependency that 404s while ``flag`` is off.

    Usage::

        router = APIRouter(
            dependencies=[Depends(require_feature("FEATURE_GROUPING"))]
        )
    """

    def _require_enabled() -> None:
        if not feature_enabled(flag):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "This feature is not available on this deployment "
                    f"({flag}=false)"
                ),
            )

    return _require_enabled
