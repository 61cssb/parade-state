"""User-facing attendance marking view.

Shows an inline-editable attendance table for the active scope (NR or a
Tagging) on the current day, with AM/PM status + remarks columns.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import and_, select

from parade_state.api.access_control import (
    get_user_accessible_groupings,
    verify_grouping_access_or_admin,
)
from parade_state.api.attendance import attendance_counts_for_date
from parade_state.api.subunit_access import (
    get_assigned_subunit_1s,
    resolve_effective_subunit_1_map,
)
from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import Attendance, AttendanceScope, Personnel
from parade_state.utils import utc_dt

router = APIRouter()


@router.get("/attendance", response_class=HTMLResponse)
async def attendance_view(
    request: Request,
    nominal_roll_id: str | None = None,
    date: utc_dt.date | None = None,
):
    """Render the attendance marking page for non-admin users.

    Lists the active roster joined to today's attendance rows (AM/PM columns).
    Attendance scope must be activated for the NR before rows can be edited.
    Full polish (Subunit-1 scoping, Copy Remarks button) lands in PR 3.
    """
    current_user = await get_current_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    target_date = date or utc_dt.utcnow().date()

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Resolve the NR via the user's accessible groupings if not given.
        accessible = await get_user_accessible_groupings(
            str(current_user.id), current_user.role, db
        )

        selected_nr_id = nominal_roll_id
        if not selected_nr_id and accessible:
            selected_nr_id = accessible[0].nominal_roll_id

        scope: AttendanceScope | None = None
        if selected_nr_id:
            scope_result = await db.execute(
                select(AttendanceScope).where(
                    AttendanceScope.nominal_roll_id == selected_nr_id
                )
            )
            scope = scope_result.scalar_one_or_none()

        # Build roster + attendance rows.
        attendance_rows = []
        has_prior_attendance = False
        if selected_nr_id:
            roster_result = await db.execute(
                select(Personnel).where(
                    and_(
                        Personnel.nominal_roll_id == selected_nr_id,
                        Personnel.status == "active",
                    )
                ).order_by(
                    Personnel.unit,
                    Personnel.sub_unit_1,
                    Personnel.rank,
                    Personnel.full_name,
                )
            )
            roster = roster_result.scalars().all()

            # Filter roster to the user's assigned subunits (tagging-aware).
            # super_admin sees the whole roster.
            accessible_pids: set[str] | None = None
            if current_user.role != "super_admin":
                all_pids = [str(p.id) for p in roster]
                eff_map = await resolve_effective_subunit_1_map(
                    db,
                    all_pids,
                    scope.tagging_id if scope else None,
                )
                allowed = await get_assigned_subunit_1s(
                    db, str(current_user.id), selected_nr_id
                )
                accessible_pids = {
                    pid for pid, sub in eff_map.items() if sub in allowed
                }

            att_result = await db.execute(
                select(Attendance).where(
                    and_(
                        Attendance.nominal_roll_id == selected_nr_id,
                        Attendance.date == target_date,
                    )
                )
            )
            att_by_person = {
                a.personnel_id: a for a in att_result.scalars().all()
            }

            for person in roster:
                if accessible_pids is not None and str(person.id) not in accessible_pids:
                    continue
                record = att_by_person.get(str(person.id))
                attendance_rows.append(
                    {
                        "id": str(record.id) if record else "",
                        "personnel_id": str(person.id),
                        "rank": person.rank,
                        "category": person.category,
                        "full_name": person.full_name,
                        "unit": person.unit,
                        "sub_unit_1": person.sub_unit_1,
                        "status_am": record.status_am if record else "absent",
                        "remarks_am": record.remarks_am if record else "",
                        "status_pm": record.status_pm if record else "absent",
                        "remarks_pm": record.remarks_pm if record else "",
                    }
                )

            from sqlalchemy import func as sa_func

            has_prior_attendance = (
                await db.execute(
                    select(sa_func.count())
                    .select_from(Attendance)
                    .where(
                        Attendance.nominal_roll_id == selected_nr_id,
                        Attendance.date < target_date,
                    )
                )
            ).scalar_one() > 0

        counts = (
            await attendance_counts_for_date(selected_nr_id, target_date, db)
            if selected_nr_id
            else {"am": {"present": 0, "absent": 0, "total": 0},
                  "pm": {"present": 0, "absent": 0, "total": 0}}
        )

    env = _get_templates(request)
    template = env.get_template("attendance.html")

    html_content = template.render(
        request=request,
        user=_user_dict(current_user),
        active_page="attendance",
        groupings=[
            {"id": str(g.id), "name": g.name, "status": g.status}
            for g in accessible
        ],
        selected_nominal_roll_id=selected_nr_id or "",
        scope_activated=scope is not None,
        scope_tagging_id=(scope.tagging_id if scope else None),
        target_date=target_date,
        attendance_rows=attendance_rows,
        counts=counts,
        has_prior_attendance=has_prior_attendance,
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
