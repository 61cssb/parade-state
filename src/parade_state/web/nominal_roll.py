"""User-facing nominal roll browser view.

Shows the personnel roster for the selected nominal roll as a simple table.
Accessible to all authenticated users — the nominal roll is the unit's base
roster (org-wide reference data). Deployment-based subunit scoping is a
possible future refinement.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import ColumnElement, func, or_, select

from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import NominalRoll, Personnel

router = APIRouter()


def _base_conditions(nominal_roll_id: str) -> list[ColumnElement[bool]]:
    """Always-on conditions scoping a query to one roll's active personnel."""
    return [
        Personnel.nominal_roll_id == nominal_roll_id,
        Personnel.status == "active",
    ]


def _search_condition(search: str | None) -> ColumnElement[bool] | None:
    if not search:
        return None
    pattern = f"%{search}%"
    return or_(
        Personnel.full_name.ilike(pattern),
        Personnel.short_id.ilike(pattern),
        Personnel.rank.ilike(pattern),
    )


def _optional_conditions(
    *,
    search: str | None,
    unit: str | None,
    sub_unit_1: str | None,
    sub_unit_2: str | None,
    category: str | None,
) -> list[ColumnElement[bool]]:
    """User-supplied filters applied to both list and count queries."""
    conds: list[ColumnElement[bool]] = []
    sc = _search_condition(search)
    if sc is not None:
        conds.append(sc)
    if unit:
        conds.append(Personnel.unit == unit)
    if sub_unit_1:
        conds.append(Personnel.sub_unit_1 == sub_unit_1)
    if sub_unit_2:
        conds.append(Personnel.sub_unit_2 == sub_unit_2)
    if category:
        conds.append(Personnel.category == category)
    return conds


async def _distinct_values(
    db,
    column,
    conditions: list[ColumnElement[bool]],
) -> list[str]:
    """Return sorted distinct non-null values for a column under conditions."""
    result = await db.execute(
        select(column)
        .where(*conditions, column.is_not(None))
        .distinct()
        .order_by(column)
    )
    return [r[0] for r in result.all() if r[0]]


@router.get("/nominal-roll", response_class=HTMLResponse)
async def nominal_roll_view(
    request: Request,
    nominal_roll_id: str | None = None,
    search: str | None = None,
    unit: str | None = None,
    sub_unit_1: str | None = None,
    sub_unit_2: str | None = None,
    category: str | None = None,
):
    """Render the nominal roll browser page.

    Shows a nominal roll selector and a table of personnel for the selected
    roll. Filters: text search (rank/name/short_id), unit, sub-unit 1,
    sub-unit 2, and category (Officer / WOSE). Sub-unit dropdowns cascade off
    the selected unit / sub-unit above them.
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
                rolls=[], selected=None,
                units=[], sub_unit_1_options=[], sub_unit_2_options=[],
                personnel=[], search=search or "",
                unit=unit or "", sub_unit_1=sub_unit_1 or "",
                sub_unit_2=sub_unit_2 or "", category=category or "",
                total_count=0,
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

        base = _base_conditions(str(selected.id))
        filters = _optional_conditions(
            search=search, unit=unit, sub_unit_1=sub_unit_1,
            sub_unit_2=sub_unit_2, category=category,
        )

        # Total matching rows (before limit) for display
        count_query = (
            select(func.count())
            .select_from(Personnel)
            .where(*base, *filters)
        )
        total_count = (await db.execute(count_query)).scalar_one()

        # Fetch personnel ordered by unit, then sub-unit, then rank, then name
        query = (
            select(Personnel)
            .where(*base, *filters)
            .order_by(
                Personnel.unit, Personnel.sub_unit_1, Personnel.sub_unit_2,
                Personnel.rank, Personnel.full_name,
            )
            .limit(1000)
        )
        personnel_result = await db.execute(query)
        personnel = personnel_result.scalars().all()

        # Filter dropdowns. Units are unscoped; sub-unit 1 scopes to the
        # selected unit; sub-unit 2 scopes to unit + sub-unit 1 (cascading).
        units = await _distinct_values(db, Personnel.unit, base)
        su1_conds = base + ([Personnel.unit == unit] if unit else [])
        sub_unit_1_options = await _distinct_values(
            db, Personnel.sub_unit_1, su1_conds
        )
        su2_conds = su1_conds + (
            [Personnel.sub_unit_1 == sub_unit_1] if sub_unit_1 else []
        )
        sub_unit_2_options = await _distinct_values(
            db, Personnel.sub_unit_2, su2_conds
        )

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
        sub_unit_1_options=sub_unit_1_options,
        sub_unit_2_options=sub_unit_2_options,
        personnel=personnel_data,
        search=search or "",
        unit=unit or "",
        sub_unit_1=sub_unit_1 or "",
        sub_unit_2=sub_unit_2 or "",
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
    sub_unit_1_options: list,
    sub_unit_2_options: list,
    personnel: list,
    search: str,
    unit: str,
    sub_unit_1: str,
    sub_unit_2: str,
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
        sub_unit_1_options=sub_unit_1_options,
        sub_unit_2_options=sub_unit_2_options,
        personnel=personnel,
        search=search,
        unit=unit,
        sub_unit_1=sub_unit_1,
        sub_unit_2=sub_unit_2,
        category=category,
        total_count=total_count,
    )
    return HTMLResponse(content=html)
