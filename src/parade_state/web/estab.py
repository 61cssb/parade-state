"""User-facing estab browser view.

Shows the personnel roster for the selected estab as a simple table.
Accessible to all authenticated users — the estab is the unit's base roster
(org-wide reference data). Deployment-based subunit scoping is a possible
future refinement.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, or_, select

from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import Estab, Personnel

router = APIRouter()


@router.get("/estab", response_class=HTMLResponse)
async def estab_view(
    request: Request,
    estab_id: str | None = None,
    search: str | None = None,
    unit: str | None = None,
):
    """Render the estab browser page.

    Shows an estab selector and a table of personnel for the selected estab.
    Optional filters: text search (rank/name/pers_no) and unit filter.
    """
    current_user = await get_current_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        # All estabs (org-wide reference data)
        estabs_result = await db.execute(
            select(Estab)
            .order_by(Estab.caa.desc())
        )
        all_estabs = estabs_result.scalars().all()

        if not all_estabs:
            return _render(
                request, current_user,
                estabs=[], selected=None, units=[],
                personnel=[], search=search or "", unit=unit or "",
                total_count=0,
            )

        # Resolve selected estab (default to most recent)
        selected = None
        if estab_id:
            for e in all_estabs:
                if str(e.id) == estab_id:
                    selected = e
                    break
        if not selected:
            # Prefer confirmed, then draft, else most recent
            non_archived = [e for e in all_estabs if e.status != "archived"]
            pool = non_archived if non_archived else all_estabs
            confirmed = [e for e in pool if e.status == "confirmed"]
            selected = confirmed[0] if confirmed else pool[0]

        # Personnel query for selected estab
        query = (
            select(Personnel)
            .where(
                Personnel.estab_id == str(selected.id),
                Personnel.status == "active",
            )
        )

        # Optional search filter
        if search:
            pattern = f"%{search}%"
            query = query.where(
                or_(
                    Personnel.full_name.ilike(pattern),
                    Personnel.pers_no.ilike(pattern),
                    Personnel.rank.ilike(pattern),
                )
            )

        # Optional unit filter
        if unit:
            query = query.where(Personnel.unit == unit)

        # Total matching rows (before limit) for display
        count_query = (
            select(func.count())
            .select_from(Personnel)
            .where(
                Personnel.estab_id == str(selected.id),
                Personnel.status == "active",
            )
        )
        if search:
            pattern = f"%{search}%"
            count_query = count_query.where(
                or_(
                    Personnel.full_name.ilike(pattern),
                    Personnel.pers_no.ilike(pattern),
                    Personnel.rank.ilike(pattern),
                )
            )
        if unit:
            count_query = count_query.where(Personnel.unit == unit)
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
                Personnel.estab_id == str(selected.id),
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
            "full_name": p.full_name,
            "pers_no": p.pers_no,
            "unit": p.unit,
            "sub_unit_1": p.sub_unit_1,
            "sub_unit_2": p.sub_unit_2,
            "sub_unit_3": p.sub_unit_3,
        }
        for p in personnel
    ]

    return _render(
        request, current_user,
        estabs=[
            {
                "id": str(e.id),
                "caa": e.caa,
                "status": e.status,
                "personnel_count": e.personnel_count,
            }
            for e in all_estabs
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
        total_count=total_count,
    )


def _render(
    request: Request,
    user,
    *,
    estabs: list,
    selected,
    units: list,
    personnel: list,
    search: str,
    unit: str,
    total_count: int,
) -> HTMLResponse:
    templates_dir = request.app.state.templates_dir
    env = Environment(
        loader=FileSystemLoader(templates_dir),
        autoescape=False,
        cache_size=0,
    )
    template = env.get_template("estab.html")
    html = template.render(
        request=request,
        user={
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "role": user.role,
        },
        active_page="estab",
        estabs=estabs,
        selected_estab=selected,
        units=units,
        personnel=personnel,
        search=search,
        unit=unit,
        total_count=total_count,
    )
    return HTMLResponse(content=html)
