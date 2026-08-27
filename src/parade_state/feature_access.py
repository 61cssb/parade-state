"""Role-level feature-access matrix (issue 37).

Layers a per-role visibility matrix on top of the deployment-wide
``FEATURE_*`` env kill switches (see :mod:`parade_state.features`).
Super-admins configure, per feature, whether the ``admin`` role can see
and use it — edited in Settings, stored in the ``feature_access`` table.

Precedence: effective visibility = ``FEATURE_*`` env flag AND matrix
entry. The env flag stays the global kill switch (flag off = 404 for
everyone, matrix state irrelevant); the matrix can only further restrict
non-super-admin roles (matrix off = 403 for that role).

Semantics:

- **Fail-open:** an absent matrix row means *enabled*. An empty table
  behaves exactly like the pre-matrix deployment, so the trial needs one
  seeded row (``upload_nr → off``) instead of seven.
- **Not a security boundary:** the matrix is a visibility tweak; the
  role checks at each page/API edge remain the actual gates. It fails
  open by design.
- ``super_admin`` is never configurable — always sees everything.

The matrix is a tiny table (≤ one row per feature per role) read on
request; no cache layer. :class:`FeatureAccessMiddleware` loads it once
per page request onto ``request.state.feature_access`` so templates and
page-route gates share one read; API edges use
:func:`require_feature_access` instead.
"""

import logging
from typing import NamedTuple

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from parade_state.auth.dependencies import require_authenticated_user
from parade_state.db import get_db_session, get_session_maker
from parade_state.models.access import FeatureAccess, User

logger = logging.getLogger(__name__)


class MatrixFeature(NamedTuple):
    """One configurable entry of the Settings feature-access card."""

    key: str
    label: str
    env_flag: str
    #: Shown on the Settings card when the surface is already hard-gated
    #: super-admin-only (a matrix toggle for admins is then inert).
    note: str = ""


#: The feature keys super-admins can toggle for the admin role.
#: ``upload_nr`` is deliberately distinct from ``nominal_roll``: Upload NR
#: lives under FEATURE_NOMINALROLL for the env kill switch, but the matrix
#: must be able to hide Upload NR from admins while keeping the NR browser
#: visible (the trial's immediate need).
MATRIX_FEATURES: tuple[MatrixFeature, ...] = (
    MatrixFeature("upload_nr", "Upload NR", "FEATURE_NOMINALROLL"),
    MatrixFeature("nominal_roll", "Nominal Roll", "FEATURE_NOMINALROLL"),
    MatrixFeature("attendance", "Attendance", "FEATURE_ATTENDANCE"),
    MatrixFeature("strength", "Unit Strength", "FEATURE_STRENGTH"),
    MatrixFeature("grouping", "Grouping", "FEATURE_GROUPING"),
    MatrixFeature(
        "deferments",
        "Deferments",
        "FEATURE_DEFERMENTS",
        note="Super-admin only today — the admin toggle has no effect.",
    ),
    MatrixFeature(
        "discussions",
        "Discussions",
        "FEATURE_DISCUSSIONS",
        note="Super-admin only today — the admin toggle has no effect.",
    ),
)

MATRIX_FEATURE_KEYS = frozenset(f.key for f in MATRIX_FEATURES)

FEATURE_KEY_LABELS = {f.key: f.label for f in MATRIX_FEATURES}


async def feature_access_map(db: AsyncSession) -> dict[str, dict[str, bool]]:
    """All matrix rows as ``{role: {feature_key: enabled}}``.

    One tiny SELECT; absence of a (role, key) entry means enabled
    (fail-open), so only explicitly configured pairs appear.
    """
    rows = (await db.execute(select(FeatureAccess))).scalars().all()
    matrix: dict[str, dict[str, bool]] = {}
    for row in rows:
        matrix.setdefault(row.role, {})[row.feature_key] = bool(row.enabled)
    return matrix


async def feature_enabled_for(db: AsyncSession, role: str, key: str) -> bool:
    """Whether ``key`` is enabled for ``role`` (absent row = enabled).

    Super-admins bypass the matrix entirely. Note this does *not* check
    the ``FEATURE_*`` env flag — callers gate on that separately (env
    off outranks everything).
    """
    if role == "super_admin":
        return True
    row = await db.scalar(
        select(FeatureAccess).where(
            FeatureAccess.feature_key == key, FeatureAccess.role == role
        )
    )
    return True if row is None else bool(row.enabled)


def feature_allowed(request: Request, role: str, key: str) -> bool:
    """Synchronous page-route/Jinja counterpart of :func:`feature_enabled_for`.

    Reads the per-request matrix snapshot stashed by
    :class:`FeatureAccessMiddleware` (``request.state.feature_access``),
    so page gates and the sidebar share the middleware's single read.
    """
    if role == "super_admin":
        return True
    matrix = getattr(request.state, "feature_access", None) or {}
    return bool(matrix.get(role, {}).get(key, True))


def require_feature_access(key: str):
    """Build a FastAPI dependency that 403s when ``key`` is matrix-off.

    The role-scoped counterpart of ``features.require_feature``: env
    flag-off 404s for everyone; matrix-off 403s for the configured role
    only. Used at router level (``include_router(dependencies=[...])``)
    for API surfaces admins can reach: personnel + nominal-rolls
    (``nominal_roll``), attendance (``attendance``), groupings
    (``grouping``).

    Usage::

        app.include_router(
            attendance.router,
            dependencies=[
                Depends(require_feature("FEATURE_ATTENDANCE")),
                Depends(require_feature_access("attendance")),
            ],
        )
    """

    async def _require_allowed(
        user: User = Depends(require_authenticated_user),
        db: AsyncSession = Depends(get_db_session),
    ) -> None:
        if await feature_enabled_for(db, user.role, key):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"{FEATURE_KEY_LABELS.get(key, key)} is not enabled "
                f"for your role"
            ),
        )

    return _require_allowed


class FeatureAccessMiddleware(BaseHTTPMiddleware):
    """Stash the matrix on ``request.state.feature_access`` for pages.

    Page routes render the sidebar (base.html) on every navigation, and
    Jinja cannot await — so the one matrix read happens here, per page
    request, and page gates + templates consume it synchronously via
    :func:`feature_allowed` / ``request.state.feature_access``. API paths
    skip it (they use :func:`require_feature_access` instead).

    Any failure (DB down, table missing mid-migration) fails open to an
    empty matrix — the matrix is not a security boundary.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not (path.startswith("/api/") or path == "/health"):
            request.state.feature_access = await self._load_matrix()
        return await call_next(request)

    @staticmethod
    async def _load_matrix() -> dict[str, dict[str, bool]]:
        session_maker = get_session_maker()
        if session_maker is None:
            return {}
        try:
            async with session_maker() as db:
                return await feature_access_map(db)
        except Exception:  # noqa: BLE001 — fail open, never block pages
            logger.warning(
                "feature_access matrix unavailable; failing open "
                "(all features enabled per-role)",
                exc_info=True,
            )
            return {}
