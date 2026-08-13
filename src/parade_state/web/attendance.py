"""User-facing attendance marking view.

Shows an inline-editable attendance table for a selected deployment and session.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select

from parade_state.api.access_control import (
    get_user_accessible_deployments,
    verify_deployment_access_or_admin,
)
from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import AttendanceRecord, Personnel
from parade_state.models import Session as SessionModel

router = APIRouter()


@router.get("/attendance", response_class=HTMLResponse)
async def attendance_view(
    request: Request,
    deployment_id: str | None = None,
    session_id: str | None = None,
):
    """Render the attendance marking page for non-admin users.

    Shows a table of personnel with inline status/remarks editing.
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
            template = env.get_template("attendance.html")
            return HTMLResponse(
                content=template.render(
                    request=request,
                    user=_user_dict(current_user),
                    active_page="attendance",
                    deployments=[],
                    selected_deployment=None,
                    sessions=[],
                    selected_session=None,
                    attendance_records=[],
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
            active_deps = [d for d in accessible if d.status == "active"]
            selected = active_deps[0] if active_deps else accessible[0]

        # Verify access
        _, has_access = await verify_deployment_access_or_admin(
            str(selected.id), str(current_user.id), current_user.role, db
        )
        if not has_access:
            return RedirectResponse(url="/auth/login", status_code=302)

        # Get sessions for this deployment
        sessions_result = await db.execute(
            select(SessionModel)
            .where(SessionModel.deployment_id == str(selected.id))
            .order_by(SessionModel.date.desc(), SessionModel.session_type)
        )
        all_sessions = sessions_result.scalars().all()

        # Resolve selected session
        selected_session = None
        if session_id:
            for s in all_sessions:
                if str(s.id) == session_id:
                    selected_session = s
                    break

        if not selected_session and all_sessions:
            # Default to most recent open session
            open_sessions = [s for s in all_sessions if s.status == "open"]
            selected_session = open_sessions[0] if open_sessions else all_sessions[0]

        # Query attendance records joined with Personnel
        attendance_rows = []
        if selected_session:
            rows_result = await db.execute(
                select(AttendanceRecord, Personnel)
                .join(Personnel, AttendanceRecord.personnel_id == Personnel.id)
                .where(AttendanceRecord.session_id == str(selected_session.id))
                .order_by(Personnel.rank, Personnel.full_name)
            )
            for record, person in rows_result.all():
                attendance_rows.append(
                    {
                        "id": str(record.id),
                        "rank": person.rank,
                        "category": person.category,
                        "full_name": person.full_name,
                        "unit": record.unit_snapshot or person.unit,
                        "sub_unit_1": record.sub_unit_1_snapshot
                        or person.sub_unit_1,
                        "sub_unit_2": record.sub_unit_2_snapshot
                        or person.sub_unit_2,
                        "status": record.status,
                        "remarks": record.remarks or "",
                    }
                )

    env = _get_templates(request)
    template = env.get_template("attendance.html")

    html_content = template.render(
        request=request,
        user=_user_dict(current_user),
        active_page="attendance",
        deployments=[
            {"id": str(d.id), "name": d.name, "status": d.status} for d in accessible
        ],
        selected_deployment={
            "id": str(selected.id),
            "name": selected.name,
            "status": selected.status,
        },
        sessions=[
            {
                "id": str(s.id),
                "date": s.date,
                "session_type": s.session_type,
                "status": s.status,
            }
            for s in all_sessions
        ],
        selected_session=(
            {
                "id": str(selected_session.id),
                "date": selected_session.date,
                "session_type": selected_session.session_type,
                "status": selected_session.status,
            }
            if selected_session
            else None
        ),
        attendance_records=attendance_rows,
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
