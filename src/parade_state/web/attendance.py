"""User-facing attendance marking view.

Attendance is always taken against a **Nominal Roll** (with the active
scope's Tagging applied) — never against a Grouping. Groupings are a
separate feature and are not linked to attendance. Shows an
inline-editable attendance table for the active scope on the current
day, with AM/PM status + remarks columns.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import and_, func, select

from parade_state.api.attendance import attendance_counts_for_date
from parade_state.api.subunit_access import (
    get_assigned_subunit_1s,
    resolve_effective_subunit_1_map,
)
from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import (
    Attendance,
    AttendanceScope,
    NominalRoll,
    Personnel,
    TaggingEntry,
)
from parade_state.utils import utc_dt

router = APIRouter()


@router.get("/attendance", response_class=HTMLResponse)
async def attendance_view(
    request: Request,
    nominal_roll_id: str | None = None,
    date: utc_dt.date | None = None,
):
    """Render the attendance marking page.

    Lists the active roster (with the active scope's tagging overlay
    applied) joined to the selected day's attendance rows (AM/PM columns).
    Attendance scope must be activated for the NR before rows can be edited.
    Non-super-admins only see personnel whose effective sub_unit_1 matches
    one of their UserSubunitAssignment rows on the NR.
    """
    current_user = await get_current_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    target_date = date or utc_dt.utcnow().date()

    session_maker = get_session_maker()
    async with session_maker() as db:
        # All NRs for the selector. Attendance is NR-scoped — groupings
        # play no part in choosing or accessing the roster.
        all_rolls = (
            (
                await db.execute(
                    select(NominalRoll).order_by(NominalRoll.caa.desc())
                )
            )
            .scalars()
            .all()
        )

        # Resolve the selected NR (default: most recent confirmed, then
        # draft, else newest — matches the nominal roll browser).
        selected = None
        if nominal_roll_id:
            for r in all_rolls:
                if str(r.id) == nominal_roll_id:
                    selected = r
                    break
        if selected is None:
            non_archived = [r for r in all_rolls if r.status != "archived"]
            pool = non_archived if non_archived else all_rolls
            confirmed = [r for r in pool if r.status == "confirmed"]
            selected = confirmed[0] if confirmed else pool[0] if pool else None

        selected_nr_id = str(selected.id) if selected else None

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
        no_assignments = False
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

            # Tagging overlay for the active scope: effective unit/sub_unit_1
            # come from the scope tagging's entries when present.
            entry_by_person: dict[str, TaggingEntry] = {}
            if scope and scope.tagging_id:
                entries = (
                    await db.execute(
                        select(TaggingEntry).where(
                            TaggingEntry.tagging_id == scope.tagging_id
                        )
                    )
                ).scalars().all()
                entry_by_person = {str(e.personnel_id): e for e in entries}

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
                no_assignments = not allowed

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
                entry = entry_by_person.get(str(person.id))
                attendance_rows.append(
                    {
                        "id": str(record.id) if record else "",
                        "personnel_id": str(person.id),
                        "rank": person.rank,
                        "category": person.category,
                        "full_name": person.full_name,
                        "unit": entry.to_unit if entry else person.unit,
                        "sub_unit_1": (
                            entry.to_sub_unit_1
                            if entry
                            else person.sub_unit_1
                        ),
                        "is_changed": entry is not None,
                        "status_am": record.status_am if record else "absent",
                        "remarks_am": record.remarks_am if record else "",
                        "status_pm": record.status_pm if record else "absent",
                        "remarks_pm": record.remarks_pm if record else "",
                    }
                )

            has_prior_attendance = (
                await db.execute(
                    select(func.count())
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
        nominal_rolls=[
            {
                "id": str(r.id),
                "caa": r.caa,
                "status": r.status,
                "personnel_count": r.personnel_count,
            }
            for r in all_rolls
        ],
        selected_nominal_roll_id=selected_nr_id or "",
        scope_activated=scope is not None,
        scope_tagging_id=(scope.tagging_id if scope else None),
        target_date=target_date,
        attendance_rows=attendance_rows,
        counts=counts,
        has_prior_attendance=has_prior_attendance,
        no_assignments=no_assignments,
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
