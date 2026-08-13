"""User-facing nominal roll browser view.

Shows the personnel roster for the selected nominal roll as a simple table.
Accessible to all authenticated users — the nominal roll is the unit's base
roster (org-wide reference data). Deployment-based subunit scoping is a
possible future refinement.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, or_, select

from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import NominalRoll, Personnel

router = APIRouter()


@router.get("/nominal-roll", response_class=HTMLResponse)
async def nominal_roll_view(
    request: Request,
    nominal_roll_id: str | None = None,
    search: str | None = None,
    unit: str | None = None,
    category: str | None = None,
):
    """Render the nominal roll browser page.

    Shows a nominal roll selector and a table of personnel for the selected
    roll. Optional filters: text search (rank/name/short_id), unit filter,
    and category filter (Officer / WOSE).
    """
    current_user = await get_current_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        # All nominal rolls (org-wide reference data)
        rolls_result = await db.execute(
            select(NominalRoll).order_by(NominalRoll.caa.desc())
        )
        all_rolls = rolls_result.scalars().all()

        if not all_rolls:
            return _render(
                request, current_user,
                rolls=[], selected=None, units=[],
                personnel=[], search=search or "", unit=unit or "",
                category=category or "", total_count=0,
            )

        # Resolve selected nominal roll (default to most recent)
        selected = None
        if nominal_roll_id:
            for r in all_rolls:
                if str(r.id) == nominal_roll_id:
                    selected = r
                    break
        if not selected:
            # Prefer confirmed, then draft, else most recent
            non_archived = [r for r in all_rolls if r.status != "archived"]
            pool = non_archived if non_archived else all_rolls
            confirmed = [r for r in pool if r.status == "confirmed"]
            selected = confirmed[0] if confirmed else pool[0]

        # Personnel query for selected nominal roll
        query = (
            select(Personnel)
            .where(
                Personnel.nominal_roll_id == str(selected.id),
                Personnel.status == "active",
            )
        )

        # Optional search filter
        if search:
            pattern = f"%{search}%"
            query = query.where(
                or_(
                    Personnel.full_name.ilike(pattern),
                    Personnel.short_id.ilike(pattern),
                    Personnel.rank.ilike(pattern),
                )
            )

        # Optional unit filter
        if unit:
            query = query.where(Personnel.unit == unit)

        # Optional category filter (Officer / WOSE)
        if category:
            query = query.where(Personnel.category == category)

        # Total matching rows (before limit) for display
        count_query = (
            select(func.count())
            .select_from(Personnel)
            .where(
                Personnel.nominal_roll_id == str(selected.id),
                Personnel.status == "active",
            )
        )
        if search:
            pattern = f"%{search}%"
            count_query = count_query.where(
                or_(
                    Personnel.full_name.ilike(pattern),
                    Personnel.short_id.ilike(pattern),
                    Personnel.rank.ilike(pattern),
                )
            )
        if unit:
            count_query = count_query.where(Personnel.unit == unit)
        if category:
            count_query = count_query.where(Personnel.category == category)
        total_count = (await db.execute(count_query)).scalar_one()

        # Fetch personnel ordered by unit, then rank, then name
        query = query.order_by(
            Personnel.unit, Personnel.sub_unit_1, Personnel.rank, Personnel.full_name
        ).limit(1000)
        personnel_result = await db.execute(query)
        personnel = personnel_result.scalars().all()

        # Distinct units for the filter dropdown
        units_result = await db.execute(
            select(Personnel.unit)
            .where(
                Personnel.nominal_roll_id == str(selected.id),
                Personnel.status == "active",
                Personnel.unit.is_not(None),
            )
            .distinct()
            .order_by(Personnel.unit)
        )
        units = [r[0] for r in units_result.all() if r[0]]

    personnel_data = [
        {
            "rank": p.rank,
            "category": p.category,
            "full_name": p.full_name,
            "short_id": p.short_id,
            "unit": p.unit,
            "sub_unit_1": p.sub_unit_1,
            "sub_unit_2": p.sub_unit_2,
            "sub_unit_3": p.sub_unit_3,
        }
        for p in personnel
    ]

    return _render(
        request, current_user,
        rolls=[
            {
                "id": str(r.id),
                "caa": r.caa,
                "status": r.status,
                "personnel_count": r.personnel_count,
            }
            for r in all_rolls
        ],
        selected={
            "id": str(selected.id),
            "caa": selected.caa,
            "status": selected.status,
            "personnel_count": selected.personnel_count,
        },
        units=units,
        personnel=personnel_data,
        search=search or "",
        unit=unit or "",
        category=category or "",
        total_count=total_count,
    )


def _render(
    request: Request,
    user,
    *,
    rolls: list,
    selected,
    units: list,
    personnel: list,
    search: str,
    unit: str,
    category: str,
    total_count: int,
) -> HTMLResponse:
    templates_dir = request.app.state.templates_dir
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=False,
        cache_size=0,
    )
    template = env.get_template("nominal_roll.html")
    html = template.render(
        request=request,
        user={
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "role": user.role,
        },
        active_page="nominal-roll",
        nominal_rolls=rolls,
        selected_nominal_roll=selected,
        units=units,
        personnel=personnel,
        search=search,
        unit=unit,
        category=category,
        total_count=total_count,
    )
    return HTMLResponse(content=html)
