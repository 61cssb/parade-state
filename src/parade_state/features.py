"""Env-var feature flags.

Feature flags hide features that are not ready for a deployment (e.g.
Deferments, Grouping) — not just from the navigation, but entirely: page
routes and API routes return 404 for every role, including super admins.
The gate sits above role checks; see issues/18-urgent-feature-flags.md.

Flags live on :class:`parade_state.config.Settings` as ``FEATURE_<NAME>``
booleans, default off, and are enabled per environment via env vars
(dev and prod are separate Railway environments with separate env vars).
Toggling a flag is an env-var change plus a service restart.

The exception is the core-feature kill switches (FEATURE_NOMINALROLL,
FEATURE_ATTENDANCE): shipped features must never disappear because an env
var is missing, so these default ON — explicit ``false`` takes the feature
offline (issue 23).

Adding a new flag is a one-line ``Settings`` change plus gating:

- routes: ``APIRouter(dependencies=[Depends(require_feature("FEATURE_X"))])``
  or ``include_router(..., dependencies=[...])`` / route-decorator
  ``dependencies=[...]`` for a single route
- nav/templates: ``{% if request.app.state.settings.FEATURE_X %}`` (the
  application factory stores the live ``Settings`` instance on
  ``app.state`` for exactly this)
"""

from fastapi import Request

from parade_state.config import get_settings

#: Friendly names for flag-gated features, shown to users on the
#: feature-disabled page. Unknown flags fall back to the raw name.
FEATURE_LABELS = {
    "FEATURE_DEFERMENTS": "Deferments",
    "FEATURE_GROUPING": "Grouping",
    "FEATURE_STRENGTH": "Unit Strength",
    "FEATURE_DISCUSSIONS": "Discussions",
    "FEATURE_NOMINALROLL": "Nominal Roll",
    "FEATURE_ATTENDANCE": "Attendance",
}


def feature_label(flag: str) -> str:
    """Human-readable feature name for ``flag``."""
    return FEATURE_LABELS.get(flag, flag)


def feature_enabled(flag: str) -> bool:
    """Whether the ``Settings`` attribute named ``flag`` is enabled.

    Reads settings at call time (per request / per render) so tests can
    toggle flags by patching the cached settings instance.
    """
    return bool(getattr(get_settings(), flag))


class FeatureDisabledError(Exception):
    """A flag-gated feature was reached while its flag is off.

    Raised by :func:`require_feature`; the application factory registers
    a handler that answers API routes with the JSON 404 and page routes
    with a styled HTML 404 explaining the feature is deliberately
    disabled (so users do not mistake it for a broken link).
    """

    def __init__(self, flag: str) -> None:
        super().__init__(flag)
        self.flag = flag


def require_feature(flag: str):
    """Build a FastAPI dependency that 404s while ``flag`` is off.

    Usage::

        router = APIRouter(
            dependencies=[Depends(require_feature("FEATURE_GROUPING"))]
        )
    """

    async def _require_enabled(request: Request) -> None:  # noqa: ARG001
        if not feature_enabled(flag):
            raise FeatureDisabledError(flag)

    return _require_enabled
