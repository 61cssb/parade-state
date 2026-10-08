"""User-facing IPPT monitoring pages (local-testing-only; FEATURE_IPPT).

- ``/ippt/dashboard`` — escalation view over all personnel: colour-coded
  top-bar tabs filter the three tiers (3 months before window close =
  red/most urgent, 6 = orange, 9 = yellow), plus the fulfilled set,
  snapshot history, and match-quality counts. Admin+.
- ``/ippt/window/{personnel_id}`` — one person's window, trajectory and
  screening history (linked from the dashboard's roster rows). Admin+.
- ``/ippt/upload`` — the six-file snapshot upload, super-admin-only
  (plain admins get the in-page no-access message, like Taggings).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

from parade_state.admin_routes import no_permission_response
from parade_state.auth.admin_dependencies import get_current_admin_user_optional
from parade_state.db import get_session_maker
from parade_state.services import ippt as ippt_service

router = APIRouter()

# Tabs render most-urgent first; the tier *keys* are named for months
# before window close (tier_3 = ≤3 months left = red = most urgent).
TIER_SECTION_ORDER = ("tier_3", "tier_6", "tier_9")
TIER_TITLES = {
    "tier_3": "3 months before window close",
    "tier_6": "6 months before window close",
    "tier_9": "9 months before window close",
}
TIER_SUBTITLES = {
    "tier_3": "most urgent — obligation unfulfilled, window about to close",
    "tier_6": "no real progress yet",
    "tier_9": "least urgent — no preparatory action yet",
}
TIER_COLOURS = {
    "tier_3": {"solid": "#dc2626", "tint": "#fef2f2", "text": "#b91c1c"},
    "tier_6": {"solid": "#ea580c", "tint": "#fff7ed", "text": "#c2410c"},
    "tier_9": {"solid": "#ca8a04", "tint": "#fefce8", "text": "#a16207"},
}


@router.get("/ippt/dashboard", response_class=HTMLResponse)
async def ippt_dashboard(request: Request, tier: str | None = None):
    """Render the IPPT monitoring dashboard (``?tier=`` selects the tab)."""
    current_user = await get_current_admin_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Tab filter: unknown values fall back to the most urgent tier.
    active_tier = tier if tier in TIER_SECTION_ORDER else TIER_SECTION_ORDER[0]

    session_maker = get_session_maker()
    async with session_maker() as db:
        dashboard = await ippt_service.dashboard_data(db)
        history = await ippt_service.snapshot_history(db)

    env = _get_templates(request)
    template = env.get_template("ippt_dashboard.html")
    return HTMLResponse(
        template.render(
            request=request,
            user=_user_dict(current_user),
            active_page="ippt",
            dashboard=dashboard,
            tier_order=TIER_SECTION_ORDER,
            tier_titles=TIER_TITLES,
            tier_subtitles=TIER_SUBTITLES,
            tier_colours=TIER_COLOURS,
            active_tier=active_tier,
            history=history,
        )
    )


@router.get("/ippt/window/{personnel_id}", response_class=HTMLResponse)
async def ippt_window(request: Request, personnel_id: str):
    """Render one person's IPPT window page."""
    current_user = await get_current_admin_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        detail = await ippt_service.window_detail(db, personnel_id)

    env = _get_templates(request)
    if detail is None:
        # Unknown id or no IPPT linkage: say so inside the page shell.
        template = env.get_template("ippt_window.html")
        return HTMLResponse(
            template.render(
                request=request,
                user=_user_dict(current_user),
                active_page="ippt",
                detail=None,
            ),
            status_code=404,
        )

    template = env.get_template("ippt_window.html")
    return HTMLResponse(
        template.render(
            request=request,
            user=_user_dict(current_user),
            active_page="ippt",
            detail=detail,
        )
    )


@router.get("/ippt/upload", response_class=HTMLResponse)
async def ippt_upload(request: Request):
    """Render the six-file snapshot upload page (super-admin-only)."""
    current_user = await get_current_admin_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)
    if current_user.role != "super_admin":
        return no_permission_response(request, current_user, "IPPT Upload", "ippt")

    session_maker = get_session_maker()
    async with session_maker() as db:
        history = await ippt_service.snapshot_history(db)
        quarantine = await ippt_service.quarantined_rows(db)
        excluded = await ippt_service.excluded_servicemen(db)

    env = _get_templates(request)
    template = env.get_template("ippt_upload.html")
    return HTMLResponse(
        template.render(
            request=request,
            user=_user_dict(current_user),
            active_page="ippt",
            history=history,
            quarantine=quarantine,
            excluded=excluded,
        )
    )


def _user_dict(user) -> dict:
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "role": user.role,
    }


def _get_templates(request: Request) -> Environment:
    templates_dir = request.app.state.templates_dir
    return Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=False,
        cache_size=0,
    )
