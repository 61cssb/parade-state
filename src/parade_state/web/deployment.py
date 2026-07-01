"""User-facing deployment summary view.

Shows deployment-level attendance summary with AM/PM session counts
and unit breakdown for the current day.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, select

from parade_state.api.access_control import (
    get_user_accessible_deployments,
    verify_deployment_access_or_admin,
)
from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import AttendanceRecord
from parade_state.models import Session as SessionModel
from parade_state.utils import utc_dt

router = APIRouter()


@router.get("/deployment", response_class=HTMLResponse)
async def deployment_view(
    request: Request,
    deployment_id: str | None = None,
):
    """Render the deployment summary page for non-admin users.

    Shows today's AM/PM session attendance counts and unit breakdown.
    """
    current_user = await get_current_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Get deployments the user can access
        accessible = await get_user_accessible_deployments(
            str(current_user.id), current_user.role, db
        )

        if not accessible:
            env = _get_templates(request)
            template = env.get_template("deployment.html")
            return HTMLResponse(
                content=template.render(
                    request=request,
                    user=_user_dict(current_user),
                    active_page="deployment",
                    deployments=[],
                    selected_deployment=None,
                    sessions=[],
                    unit_breakdown=[],
                )
            )

        # Resolve selected deployment
        selected = None
        if deployment_id:
            for d in accessible:
                if str(d.id) == deployment_id:
                    selected = d
                    break

        if not selected:
            # Default to most recent active deployment
            active_deps = [d for d in accessible if d.status == "active"]
            if active_deps:
                selected = active_deps[0]
            else:
                selected = accessible[0]

        # Verify access (redundant but consistent with plan)
        _, has_access = await verify_deployment_access_or_admin(
            str(selected.id), str(current_user.id), current_user.role, db
        )
        if not has_access:
            return RedirectResponse(url="/auth/login", status_code=302)

        # Query today's sessions for this deployment
        today = utc_dt.utcnow().date()
        sessions_result = await db.execute(
            select(SessionModel)
            .where(
                SessionModel.deployment_id == str(selected.id),
                SessionModel.date == today,
            )
            .order_by(SessionModel.session_type)
        )
        sessions = sessions_result.scalars().all()

        # For each session, get attendance counts by status
        sessions_data = []
        for s in sessions:
            counts_result = await db.execute(
                select(
                    AttendanceRecord.status,
                    func.count(AttendanceRecord.id),
                )
                .where(AttendanceRecord.session_id == str(s.id))
                .group_by(AttendanceRecord.status)
            )
            counts = {row[0]: row[1] for row in counts_result.all()}
            total = sum(counts.values())
            sessions_data.append(
                {
                    "id": str(s.id),
                    "session_type": s.session_type,
                    "status": s.status,
                    "present": counts.get("present", 0),
                    "absent": counts.get("absent", 0),
                    "excused": counts.get("excused", 0),
                    "total": total,
                }
            )

        # Unit breakdown: group attendance by unit_snapshot for today's sessions
        session_ids = [s["id"] for s in sessions_data]
        unit_rows = []
        if session_ids:
            unit_result = await db.execute(
                select(
                    AttendanceRecord.unit_snapshot,
                    AttendanceRecord.status,
                    func.count(AttendanceRecord.id),
                )
                .where(AttendanceRecord.session_id.in_(session_ids))
                .group_by(
                    AttendanceRecord.unit_snapshot,
                    AttendanceRecord.status,
                )
                .order_by(AttendanceRecord.unit_snapshot)
            )
            # Aggregate into per-unit dict
            unit_map: dict[str, dict] = {}
            for unit, status_val, count_val in unit_result.all():
                key = unit or "—"
                if key not in unit_map:
                    unit_map[key] = {
                        "unit": key,
                        "present": 0,
                        "absent": 0,
                        "excused": 0,
                        "total": 0,
                    }
                unit_map[key][status_val] = unit_map[key].get(status_val, 0) + count_val
                unit_map[key]["total"] += count_val
            unit_rows = list(unit_map.values())

    env = _get_templates(request)
    template = env.get_template("deployment.html")

    html_content = template.render(
        request=request,
        user=_user_dict(current_user),
        active_page="deployment",
        deployments=[
            {"id": str(d.id), "name": d.name, "status": d.status} for d in accessible
        ],
        selected_deployment={
            "id": str(selected.id),
            "name": selected.name,
            "status": selected.status,
        },
        sessions=sessions_data,
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
