"""User-facing grouping browser view (issue 26 redesign).

Shows the groupings based on the nominal roll active for attendance:
a dropdown selects the grouping, and the roster table carries each
serviceman's group(s), checkbox and remarks. Visible to all
authenticated users; every mutation is super-admin only and enforced
server-side at the API. Groupings never read or write attendance.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from parade_state.auth.admin_dependencies import get_current_user_optional
from parade_state.db import get_session_maker
from parade_state.models import (
    Grouping,
    GroupingMemberState,
    GroupingMembership,
    NominalRoll,
    Personnel,
)

router = APIRouter()


@router.get("/grouping", response_class=HTMLResponse)
async def grouping_view(
    request: Request,
    grouping_id: str | None = None,
):
    """Render the grouping browser page."""
    current_user = await get_current_user_optional(request)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        active_nr = (
            await db.execute(
                select(NominalRoll).where(NominalRoll.attendance_active.is_(True))
            )
        ).scalar_one_or_none()

        groupings: list[Grouping] = []
        selected: Grouping | None = None
        previous_nr = None
        previous_groupings: list[Grouping] = []

        if active_nr is not None:
            groupings = list(
                (
                    await db.execute(
                        select(Grouping)
                        .where(Grouping.nominal_roll_id == active_nr.id)
                        .options(selectinload(Grouping.groups))
                        .order_by(Grouping.created_at)
                    )
                )
                .scalars()
                .all()
            )
            if grouping_id:
                selected = next(
                    (g for g in groupings if str(g.id) == grouping_id), None
                )
            if selected is None and groupings:
                selected = groupings[0]

            # Copy-from-previous offer: only meaningful on a blank slate.
            if not groupings and active_nr.attendance_activated_at is not None:
                previous_nr = (
                    await db.execute(
                        select(NominalRoll)
                        .where(
                            NominalRoll.attendance_activated_at.is_not(None),
                            NominalRoll.attendance_activated_at
                            < active_nr.attendance_activated_at,
                        )
                        .order_by(NominalRoll.attendance_activated_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if previous_nr is not None:
                    previous_groupings = list(
                        (
                            await db.execute(
                                select(Grouping)
                                .where(
                                    Grouping.nominal_roll_id == previous_nr.id
                                )
                                .order_by(Grouping.created_at)
                            )
                        )
                        .scalars()
                        .all()
                    )

        personnel_rows: list[dict] = []
        selected_groups: list[dict] = []
        if selected is not None:
            group_rows = list(
                (
                    await db.execute(
                        select(GroupingMembership).where(
                            GroupingMembership.grouping_id == selected.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            groups_by_person: dict[str, list[str]] = {}
            for membership in group_rows:
                groups_by_person.setdefault(
                    membership.personnel_id, []
                ).append(membership.group_id)

            state_rows = (
                await db.execute(
                    select(GroupingMemberState).where(
                        GroupingMemberState.grouping_id == selected.id
                    )
                )
            ).scalars().all()
            state_by_person = {
                state.personnel_id: state for state in state_rows
            }

            all_groups = sorted(selected.groups, key=lambda g: g.position)
            member_counts: dict[str, int] = {}
            for membership in group_rows:
                member_counts[membership.group_id] = (
                    member_counts.get(membership.group_id, 0) + 1
                )
            selected_groups = [
                {
                    "id": str(group.id),
                    "label": group.label,
                    "member_count": member_counts.get(group.id, 0),
                }
                for group in all_groups
            ]

            people = (
                (
                    await db.execute(
                        select(Personnel)
                        .where(
                            Personnel.nominal_roll_id == selected.nominal_roll_id,
                            Personnel.status == "active",
                        )
                        .order_by(
                            Personnel.unit,
                            Personnel.sub_unit_1,
                            Personnel.rank,
                            Personnel.full_name,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for person in people:
                state = state_by_person.get(person.id)
                personnel_rows.append(
                    {
                        "id": str(person.id),
                        "rank": person.rank,
                        "full_name": person.full_name,
                        "unit": person.unit,
                        "sub_unit_1": person.sub_unit_1,
                        "group_ids": [
                            str(gid) for gid in groups_by_person.get(person.id, [])
                        ],
                        "checkbox": bool(state and state.checkbox),
                        "remarks": state.remarks if state else None,
                    }
                )

    env = _get_templates(request)
    template = env.get_template("grouping.html")

    html_content = template.render(
        request=request,
        user=_user_dict(current_user),
        active_page="grouping",
        active_nr=(
            {"id": str(active_nr.id), "caa": active_nr.caa}
            if active_nr is not None
            else None
        ),
        groupings=[
            {"id": str(g.id), "label": g.label} for g in groupings
        ],
        selected_grouping=(
            {
                "id": str(selected.id),
                "label": selected.label,
                "multiple_membership": selected.multiple_membership,
                "allow_ungrouped": selected.allow_ungrouped,
                "groups": selected_groups,
            }
            if selected is not None
            else None
        ),
        personnel_rows=personnel_rows,
        previous_nr=(
            {"id": str(previous_nr.id), "caa": previous_nr.caa}
            if previous_nr is not None
            else None
        ),
        previous_groupings=[
            {"id": str(g.id), "label": g.label} for g in previous_groupings
        ],
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
