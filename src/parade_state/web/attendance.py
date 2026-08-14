"""User-facing attendance marking view.

Attendance is always taken against the Nominal Roll that is currently
**active for attendance** (with its 1:1 tagging applied) — never against a
Grouping. When no NR is active, the page shows an inactive message instead
of the marking table.
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
from parade_state.api.tagging import _load_nr_tagging
from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import (
    Attendance,
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
    sub_unit_1: str | None = None,
):
    """Render the attendance marking page.

    Defaults to the NR currently active for attendance (if any). Lists the
    roster with the NR's 1:1 tagging overlay applied, joined to the selected
    day's attendance rows (AM/PM columns). Editing is enabled only when the
    selected NR is the active one. Non-super-admins only see personnel whose
    effective sub_unit_1 matches one of their UserSubunitAssignment rows on
    the NR.
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
        active_nr = next((r for r in all_rolls if r.attendance_active), None)

        # Resolve the selected NR (default: the active one, else newest).
        selected = None
        if nominal_roll_id:
            for r in all_rolls:
                if str(r.id) == nominal_roll_id:
                    selected = r
                    break
        if selected is None:
            selected = active_nr or (all_rolls[0] if all_rolls else None)

        selected_nr_id = str(selected.id) if selected else None
        attendance_active = bool(selected and selected.attendance_active)

        # Build roster + attendance rows.
        attendance_rows = []
        subunit_options: list[str] = []
        has_prior_attendance = False
        no_assignments = False
        applied_tagging_id: str | None = None
        if selected_nr_id:
            tagging = await _load_nr_tagging(db, selected_nr_id, with_entries=False)
            applied_tagging_id = str(tagging.id) if tagging else None

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

            # Tagging overlay: effective unit/sub_unit_1 come from the NR's
            # tagging entries when present.
            entry_by_person: dict[str, TaggingEntry] = {}
            if applied_tagging_id:
                entries = (
                    await db.execute(
                        select(TaggingEntry).where(
                            TaggingEntry.tagging_id == applied_tagging_id
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
                    applied_tagging_id,
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
                eff_sub1 = entry.to_sub_unit_1 if entry else person.sub_unit_1
                attendance_rows.append(
                    {
                        "id": str(record.id) if record else "",
                        "personnel_id": str(person.id),
                        "rank": person.rank,
                        "category": person.category,
                        "full_name": person.full_name,
                        "unit": entry.to_unit if entry else person.unit,
                        "sub_unit_1": eff_sub1,
                        "is_changed": entry is not None,
                        "status_am": record.status_am if record else "absent",
                        "remarks_am": record.remarks_am if record else "",
                        "status_pm": record.status_pm if record else "absent",
                        "remarks_pm": record.remarks_pm if record else "",
                    }
                )

            # Filter dropdown options: distinct effective sub_unit_1 across
            # the user's whole visible roster (before the filter is applied).
            subunit_options = sorted(
                {
                    r["sub_unit_1"]
                    for r in attendance_rows
                    if r["sub_unit_1"]
                }
            )
            if sub_unit_1:
                attendance_rows = [
                    r for r in attendance_rows if r["sub_unit_1"] == sub_unit_1
                ]

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
                "attendance_active": bool(r.attendance_active),
                "personnel_count": r.personnel_count,
            }
            for r in all_rolls
        ],
        selected_nominal_roll_id=selected_nr_id or "",
        any_active_nr=active_nr is not None,
        attendance_active=attendance_active,
        target_date=target_date,
        sub_unit_1_filter=sub_unit_1 or "",
        subunit_options=subunit_options,
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
