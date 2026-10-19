"""User-facing IPPT monitoring pages (FEATURE_IPPT-gated; the Railway
production environment force-disables the flag, see config.Settings).

- ``/ippt/dashboard`` — escalation view over all personnel: colour-coded
  top-bar tabs filter the three tiers (3 months before window close =
  red/most urgent, 6 = orange, 9 = yellow), plus the fulfilled set,
  snapshot history, and match-quality counts. A GET filter/sort bar
  narrows and orders every roster section by sub-unit, state, booked and
  FFI (all plain query params, so each view is a shareable URL). Admin+.
- ``/ippt/dashboard/export.xlsx`` — the dashboard as a five-tab Excel
  workbook (3/6/9 months, on track, fulfilled), always the full dataset
  regardless of the on-screen filters (decided 2026-10-19). Admin+.
- ``/ippt/window/{personnel_id}`` — one person's window, trajectory and
  screening history (linked from the dashboard's roster rows). Admin+.
- ``/ippt/upload`` — the six-file snapshot upload, super-admin-only
  (plain admins get the in-page no-access message, like Taggings).
"""

import io
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from jinja2 import Environment, FileSystemLoader

from parade_state.admin_routes import no_permission_response
from parade_state.auth.admin_dependencies import get_current_admin_user_optional
from parade_state.db import get_session_maker
from parade_state.services import ippt as ippt_service
from parade_state.utils import ippt_export, utc_dt

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
async def ippt_dashboard(
    request: Request,
    tier: str | None = None,
    subunit: str | None = None,
    state: str | None = None,
    booked: str | None = None,
    ffi: str | None = None,
    sort: str | None = None,
):
    """Render the IPPT monitoring dashboard (``?tier=`` selects the tab;
    ``?subunit=``/``?state=``/``?booked=``/``?ffi=`` filter and ``?sort=``
    re-orders every roster section)."""
    current_user = await get_current_admin_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    # View params: unknown values fall back to the defaults (most urgent
    # tier, base order, no filtering) — same contract as ?tier=. The
    # subunit/state params additionally validate against the observed
    # option lists once the data is loaded.
    active_tier = tier if tier in TIER_SECTION_ORDER else TIER_SECTION_ORDER[0]
    active_sort = sort if sort in ippt_service.SORT_KEYS else "urgency"
    active_booked = booked if booked in ippt_service.BOOKED_FILTERS else None
    active_ffi = ffi if ffi in ippt_service.FFI_FILTERS else None

    session_maker = get_session_maker()
    async with session_maker() as db:
        dashboard = await ippt_service.dashboard_data(db)
        history = await ippt_service.snapshot_history(db)

    servicemen = dashboard["servicemen"]
    subunit_options = sorted({e["sub_unit"] for e in servicemen if e["sub_unit"]})
    state_options = sorted(
        {(e["state"], e["state_label"]) for e in servicemen},
        key=lambda pair: pair[1],
    )
    active_subunit = subunit if subunit in subunit_options else None
    active_state = state if state in dict(state_options) else None

    def view(entries: list[dict]) -> list[dict]:
        """One roster section through the current filter + sort."""
        return ippt_service.sort_states(
            ippt_service.filter_states(
                entries,
                subunit=active_subunit,
                state=active_state,
                booked=active_booked,
                ffi=active_ffi,
            ),
            active_sort,
        )

    # Section badges show the filtered count, with the unfiltered total
    # alongside whenever a filter hides somebody ("2 of 5").
    tier_totals = {band: len(entries) for band, entries in dashboard["tiers"].items()}
    ontrack_total = sum(
        1 for e in servicemen if not e["highlighted"] and not e["fulfilled"]
    )
    fulfilled_total = len(dashboard["fulfilled"])
    dashboard["tiers"] = {
        band: view(entries) for band, entries in dashboard["tiers"].items()
    }
    dashboard["fulfilled"] = view(dashboard["fulfilled"])
    filtered_servicemen = view(servicemen)
    ontrack = [
        e for e in filtered_servicemen if not e["highlighted"] and not e["fulfilled"]
    ]
    filters_active = bool(active_subunit or active_state or active_booked or active_ffi)

    # Tier tabs keep the current filter + sort params (switching tabs must
    # not drop the view); the default sort stays out of the URLs.
    preserve = [
        ("subunit", active_subunit),
        ("state", active_state),
        ("booked", active_booked),
        ("ffi", active_ffi),
        ("sort", active_sort if active_sort != "urgency" else None),
    ]
    carried = urlencode([(key, value) for key, value in preserve if value])
    query_suffix = f"&{carried}" if carried else ""

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
            subunit_options=subunit_options,
            state_options=state_options,
            active_subunit=active_subunit,
            active_state=active_state,
            active_booked=active_booked,
            active_ffi=active_ffi,
            active_sort=active_sort,
            filters_active=filters_active,
            ontrack=ontrack,
            shown_count=len(filtered_servicemen),
            total_count=len(servicemen),
            tier_totals=tier_totals,
            ontrack_total=ontrack_total,
            fulfilled_total=fulfilled_total,
            query_suffix=query_suffix,
        )
    )


@router.get("/ippt/dashboard/export.xlsx")
async def ippt_dashboard_export(request: Request):
    """Download the dashboard as a five-tab XLSX workbook (admin+).

    Always the full dataset in base order — the on-screen filter/sort
    params do not apply (decided 2026-10-19)."""
    current_user = await get_current_admin_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        dashboard = await ippt_service.dashboard_data(db)

    payload = ippt_export.build_dashboard_workbook(dashboard)
    filename = f"ippt_dashboard_{utc_dt.utcnow().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f"attachment; filename={filename}"},
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
