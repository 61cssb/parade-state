"""User-facing nominal roll browser view.

Shows the personnel roster for the selected nominal roll as a simple table.
Accessible to all authenticated users — the nominal roll is the unit's base
roster (org-wide reference data).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import ColumnElement, func, or_, select

from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import CALLUP_STATUSES, NominalRoll, Personnel, TaggingEntry
from parade_state.utils import ranks

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
    # Search matches name and pers no only — never rank, unit,
    # sub-unit, or category (those have their own dedicated filters).
    return or_(
        Personnel.full_name.ilike(pattern),
        Personnel.pers_no.ilike(pattern),
    )


def _optional_conditions(
    *,
    search: str | None,
    unit: str | None,
    sub_unit_1: str | None,
    sub_unit_2: str | None,
    category: str | None,
    rank: str | None,
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
    if rank:
        conds.append(Personnel.rank == rank)
    return conds


_SCOPE_COLUMNS: dict[str, ColumnElement] = {
    "unit": Personnel.unit,
    "sub_unit_1": Personnel.sub_unit_1,
    "sub_unit_2": Personnel.sub_unit_2,
    "category": Personnel.category,
    "rank": Personnel.rank,
}


def _scoped_conditions(
    base: list[ColumnElement[bool]],
    **selections: str | None,
) -> list[ColumnElement[bool]]:
    """Base conditions plus an equality filter for each non-empty selection.

    Used to scope dropdown option queries: e.g. the sub-unit 1 dropdown is
    scoped by the selected unit, and the rank dropdown by unit/sub-unit/
    category so it only lists ranks present in the filtered population.
    """
    conds = list(base)
    for key, value in selections.items():
        if value:
            conds.append(_SCOPE_COLUMNS[key] == value)
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
    rank: str | None = None,
):
    """Render the nominal roll browser page.

    Shows a nominal roll selector and a table of personnel for the selected
    roll. Filters: text search (name/pers no), unit, sub-unit 1, sub-unit 2,
    category (Officer / WOSE), and rank. Sub-unit and rank dropdowns cascade
    off the filters above them so they only list values present in the
    currently-filtered population.
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
                rank_options=[],
                edit_unit_options=[], edit_sub1_options=[],
                edit_sub2_options=[], edit_sub3_options=[],
                rank_choices=[],
                callup_statuses=list(CALLUP_STATUSES),
                personnel=[], search=search or "",
                unit=unit or "", sub_unit_1=sub_unit_1 or "",
                sub_unit_2=sub_unit_2 or "", category=category or "",
                rank=rank or "",
                total_count=0,
            )

        # Resolve selected nominal roll (default to the one active for
        # attendance, else most recent)
        active_nr = next((r for r in all_rolls if r.attendance_active), None)
        selected = None
        if nominal_roll_id:
            for r in all_rolls:
                if str(r.id) == nominal_roll_id:
                    selected = r
                    break
        if not selected:
            selected = active_nr or (all_rolls[0] if all_rolls else None)

        base = _base_conditions(str(selected.id))
        filters = _optional_conditions(
            search=search, unit=unit, sub_unit_1=sub_unit_1,
            sub_unit_2=sub_unit_2, category=category, rank=rank,
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

        # Cascading dropdowns: each lists values present under the selections
        # above it. Units are unscoped; sub-unit 1 scopes to unit; sub-unit 2
        # scopes to unit + sub-unit 1; rank scopes to all of those + category.
        units = await _distinct_values(db, Personnel.unit, base)
        sub_unit_1_options = await _distinct_values(
            db, Personnel.sub_unit_1, _scoped_conditions(base, unit=unit)
        )
        sub_unit_2_options = await _distinct_values(
            db, Personnel.sub_unit_2,
            _scoped_conditions(base, unit=unit, sub_unit_1=sub_unit_1),
        )
        rank_options = await _distinct_values(
            db, Personnel.rank,
            _scoped_conditions(
                base, unit=unit, sub_unit_1=sub_unit_1,
                sub_unit_2=sub_unit_2, category=category,
            ),
        )

        # Unscoped suggestion lists for the super-admin cell editor
        # (datalist inputs also accept values not present on the NR).
        edit_unit_options = units
        edit_sub1_options = await _distinct_values(db, Personnel.sub_unit_1, base)
        edit_sub2_options = await _distinct_values(db, Personnel.sub_unit_2, base)
        edit_sub3_options = await _distinct_values(db, Personnel.sub_unit_3, base)

        # Load the NR's 1:1 tagging entries (overlay). The entry map is
        # keyed by personnel_id; each entry's ``to_*`` values override the
        # personnel's canonical unit/subunit when computing effective values.
        entry_rows = (
            await db.execute(
                select(TaggingEntry).where(
                    TaggingEntry.personnel_id.in_([p.id for p in personnel])
                )
            )
        ).scalars().all()
        entry_by_personnel = {str(e.personnel_id): e for e in entry_rows}

    personnel_data = []
    for p in personnel:
        entry = entry_by_personnel.get(str(p.id))
        is_changed = entry is not None
        personnel_data.append(
            {
                "id": str(p.id),
                "rank": p.rank,
                "category": p.category,
                "full_name": p.full_name,
                "pers_no": p.pers_no,
                "unit": entry.to_unit if entry else p.unit,
                "sub_unit_1": entry.to_sub_unit_1 if entry else p.sub_unit_1,
                "sub_unit_2": entry.to_sub_unit_2 if entry else p.sub_unit_2,
                "sub_unit_3": entry.to_sub_unit_3 if entry else p.sub_unit_3,
                "callup_status": p.callup_status,
                "remarks": p.remarks,
                "source": p.source,
                "is_changed": is_changed,
            }
        )

    return _render(
        request, current_user,
        rolls=[
            {
                "id": str(r.id),
                "caa": r.caa,
                "attendance_active": bool(r.attendance_active),
                "personnel_count": r.personnel_count,
            }
            for r in all_rolls
        ],
        selected={
            "id": str(selected.id),
            "caa": selected.caa,
            "attendance_active": bool(selected.attendance_active),
            "personnel_count": selected.personnel_count,
            "label": selected.label,
            "remarks": selected.remarks,
        },
        units=units,
        sub_unit_1_options=sub_unit_1_options,
        sub_unit_2_options=sub_unit_2_options,
        rank_options=rank_options,
        edit_unit_options=edit_unit_options,
        edit_sub1_options=edit_sub1_options,
        edit_sub2_options=edit_sub2_options,
        edit_sub3_options=edit_sub3_options,
        rank_choices=[
            ("Officer", sorted(ranks.OFFICER_RANKS)),
            ("WOSE", sorted(ranks.WOSE_RANKS)),
            ("Military Expert", [f"ME{i}" for i in range(1, 10)]),
        ],
        callup_statuses=list(CALLUP_STATUSES),
        personnel=personnel_data,
        search=search or "",
        unit=unit or "",
        sub_unit_1=sub_unit_1 or "",
        sub_unit_2=sub_unit_2 or "",
        category=category or "",
        rank=rank or "",
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
    rank_options: list,
    edit_unit_options: list,
    edit_sub1_options: list,
    edit_sub2_options: list,
    edit_sub3_options: list,
    rank_choices: list,
    callup_statuses: list,
    personnel: list,
    search: str,
    unit: str,
    sub_unit_1: str,
    sub_unit_2: str,
    category: str,
    rank: str,
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
        rank_options=rank_options,
        edit_unit_options=edit_unit_options,
        edit_sub1_options=edit_sub1_options,
        edit_sub2_options=edit_sub2_options,
        edit_sub3_options=edit_sub3_options,
        rank_choices=rank_choices,
        callup_statuses=callup_statuses,
        personnel=personnel,
        search=search,
        unit=unit,
        sub_unit_1=sub_unit_1,
        sub_unit_2=sub_unit_2,
        category=category,
        rank=rank,
        total_count=total_count,
    )
    return HTMLResponse(content=html)
