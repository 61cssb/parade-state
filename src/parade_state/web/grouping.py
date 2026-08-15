"""User-facing grouping summary view.

Shows grouping-level attendance summary with AM/PM counts and a unit
breakdown for the current day, drawn from the NR/Tagging-scoped attendance
model.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select

from parade_state.api.access_control import (
    get_user_accessible_groupings,
    verify_grouping_access_or_admin,
)
from parade_state.api.attendance import attendance_counts_for_date
from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import Attendance, Personnel
from parade_state.models.attendance import PRESENT_LIKE_STATUSES
from parade_state.utils import utc_dt

router = APIRouter()


@router.get("/grouping", response_class=HTMLResponse)
async def grouping_view(
    request: Request,
    grouping_id: str | None = None,
):
    """Render the grouping summary page for non-admin users.

    Shows today's AM/PM attendance counts and a unit breakdown.
    """
    current_user = await get_current_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Admin-only system: the viewer role is deferred, so gate the viewer
    # surface on admin role until it exists.
    if current_user.role not in ("admin", "super_admin"):
        return RedirectResponse(url="/auth/no-access", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Get groupings the user can access
        accessible = await get_user_accessible_groupings(
            str(current_user.id), current_user.role, db
        )

        if not accessible:
            env = _get_templates(request)
            template = env.get_template("grouping.html")
            return HTMLResponse(
                content=template.render(
                    request=request,
                    user=_user_dict(current_user),
                    active_page="grouping",
                    groupings=[],
                    selected_grouping=None,
                    counts=None,
                    unit_breakdown=[],
                )
            )

        # Resolve selected grouping
        selected = None
        if grouping_id:
            for g in accessible:
                if str(g.id) == grouping_id:
                    selected = g
                    break

        if not selected:
            active_groups = [g for g in accessible if g.status == "active"]
            if active_groups:
                selected = active_groups[0]
            else:
                selected = accessible[0]

        # Verify access (redundant but consistent)
        _, has_access = await verify_grouping_access_or_admin(
            str(selected.id), str(current_user.id), current_user.role, db
        )
        if not has_access:
            return RedirectResponse(url="/auth/login", status_code=302)

        today = utc_dt.utcnow().date()

        counts = await attendance_counts_for_date(
            selected.nominal_roll_id, today, db
        )

        # Unit breakdown from today's attendance rows.
        unit_result = await db.execute(
            select(Attendance).where(
                Attendance.nominal_roll_id == selected.nominal_roll_id,
                Attendance.date == today,
            )
        )
        unit_map: dict[str, dict[str, int]] = {}
        for row in unit_result.scalars().all():
            unit = row.unit_snapshot or "—"
            stats = unit_map.setdefault(unit, {"present": 0, "total": 0})
            for value in (row.status_am, row.status_pm):
                stats["total"] += 1
                if value in PRESENT_LIKE_STATUSES:
                    stats["present"] += 1
        unit_rows = [
            {
                "unit": unit,
                "present": s["present"],
                "absent": s["total"] - s["present"],
                "total": s["total"],
            }
            for unit, s in sorted(unit_map.items())
        ]

    env = _get_templates(request)
    template = env.get_template("grouping.html")

    html_content = template.render(
        request=request,
        user=_user_dict(current_user),
        active_page="grouping",
        groupings=[
            {"id": str(g.id), "name": g.name, "status": g.status} for g in accessible
        ],
        selected_grouping={
            "id": str(selected.id),
            "name": selected.name,
            "status": selected.status,
        },
        counts=counts,
        unit_breakdown=unit_rows,
        today=today,
    )

    return HTMLResponse(content=html_content)


def _user_dict(user) -> dict:
    """Build user dict for template rendering."""
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "role": user.role,
    }


def _get_templates(request: Request) -> Environment:
    """Get Jinja2 environment (singleton, matching admin_routes pattern)."""
    templates_dir = request.app.state.templates_dir
    return Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=False,
        cache_size=0,
    )
