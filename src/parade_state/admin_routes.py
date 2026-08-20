"""Admin interface routes using Jinja2 templates."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, or_, select
from urllib.parse import urlsplit

from parade_state.api.subunit_access import get_assigned_subunit_1s
from parade_state.api.tagging import _load_nr_tagging
from parade_state.auth.admin_dependencies import (
    get_current_admin_user_optional,
    require_admin_user_flexible,
)
from parade_state.db import get_session_maker
from parade_state.features import require_feature
from parade_state.models import (
    AccessLevel,
    Attendance,
    AuditLog,
    CsvUpload,
    Deferment,
    Grouping,
    NominalRoll,
    PRESENT_LIKE_STATUSES,
    Personnel,
    Tagging,
    TaggingEntry,
    User,
)
from parade_state.utils import utc_dt

router = APIRouter()
depends_admin = Depends(require_admin_user_flexible)

# Audit log filter dropdown options (mirrors AuditLog model enum values)
AUDIT_ENTITY_TYPES = [
    "attendance",
    "grouping",
    "session",
    "user",
    "csv_upload",
    "nominal_roll",
    "personnel",
    "access_level",
    "column_mapping",
]
AUDIT_ACTIONS = ["create", "update", "delete", "archive", "close", "finalize"]

# Deferment filter dropdown options (mirrors Deferment model enums)
DEFERMENT_REASONS = [
    "Honeymoon",
    "Work",
    "Full-time studies",
    "Other",
    "Medical Grounds",
    "Examination",
    "New employment",
    "Special employment",
    "Compassionate",
    "Childbirth",
    "Part-time studies",
    "Newly Established Business (Local)",
]
DEFERMENT_STATUSES = [
    "Pending action",
    "Approved",
    "Withdrawn",
    "Rejected",
    "To Resubmit",
    "Time off arrangement",
    "Not called up",
    "Do not call up",
]

# Global Jinja2 environment (singleton)
_jinja_env = None

def get_templates(request: Request) -> Environment:
    """Get Jinja2 environment singleton from app state or create if needed."""
    global _jinja_env
    if _jinja_env is None:
        templates_dir = request.app.state.templates_dir
        _jinja_env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=False,
            cache_size=0  # Disable caching completely
        )
    return _jinja_env


def _no_permission_response(
    request: Request, current_admin, page_name: str, active_page: str
) -> HTMLResponse:
    """Render the in-page no-access message for super-admin-only pages.

    Plain admins see the page shell (sidebar + topbar) with a 403 status
    instead of a silent redirect, so restricted pages stay discoverable.
    """
    env = get_templates(request)
    template = env.get_template("admin/no_permission.html")
    html = template.render(
        request=request,
        user={
            "id": str(current_admin.id),
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page=active_page,
        page_name=page_name,
    )
    return HTMLResponse(content=html, status_code=403)


def _strength_buckets() -> dict[str, dict[str, int]]:
    """Zeroed In/Current counters per personnel category (Officer, WOSE)."""
    return {
        "Officer": {"in": 0, "current": 0},
        "WOSE": {"in": 0, "current": 0},
    }


def _strength_cell(bucket: dict[str, int]) -> dict[str, int]:
    """Render-ready In/Out/Current/% cell from one In/Current counter.

    Out is the complement of Current within In (every Called Up person is
    exactly one of Current/Out — unmarked attendance counts as absent),
    and % is Current over In, whole-number, 0 when In is 0.
    """
    total_in = bucket["in"]
    current = bucket["current"]
    return {
        "in": total_in,
        "out": total_in - current,
        "current": current,
        "pct": round(current * 100 / total_in) if total_in else 0,
    }


def _strength_cells(buckets: dict[str, dict[str, int]]) -> dict:
    """Officer/WOSE/Total cells for one report row or rollup."""
    total = {
        "in": buckets["Officer"]["in"] + buckets["WOSE"]["in"],
        "current": buckets["Officer"]["current"] + buckets["WOSE"]["current"],
    }
    return {
        "officer": _strength_cell(buckets["Officer"]),
        "wose": _strength_cell(buckets["WOSE"]),
        "total": _strength_cell(total),
    }


@router.get(
    "/admin",
    response_class=HTMLResponse,
    dependencies=[Depends(require_feature("FEATURE_STRENGTH"))],
)
async def admin_unit_strength(
    request: Request,
    date: utc_dt.date | None = None,
    slot: str = "am",
):
    """Render the Unit Strength report (issue 25).

    Aggregates the parade state of the NR active for attendance by
    effective sub_unit_1/sub_unit_2 into the strength reporting format:
    Officer/WOSE/Total column groups, each In/Out/Current/%. In counts
    Called Up personnel; Current those marked present/late in the selected
    slot; Out everyone else (unmarked = absent). Unit and sub_unit_3 are
    ignored — attached personnel from other units report here too.
    Super-admins see the whole unit; regular admins see only the
    sub_unit_1 sections assigned to them on the NR.
    """
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    if slot not in ("am", "pm"):
        slot = "am"
    target_date = date or utc_dt.utcnow().date()

    sections: list[dict] = []
    total_cells: dict = {}
    no_assignments = False
    nr_label = None

    session_maker = get_session_maker()
    async with session_maker() as db:
        active_nr = (
            await db.execute(
                select(NominalRoll).where(NominalRoll.attendance_active.is_(True))
            )
        ).scalars().first()

        if active_nr is not None:
            nr_id = str(active_nr.id)
            nr_label = (
                active_nr.caa.isoformat() if active_nr.caa else nr_id[:8]
            )

            # The strength population is the attendance roster: active
            # personnel on the NR with callup status Called Up.
            roster = (
                await db.execute(
                    select(Personnel).where(
                        Personnel.nominal_roll_id == nr_id,
                        Personnel.status == "active",
                        Personnel.callup_status == "Called Up",
                    )
                )
            ).scalars().all()

            # Tagging overlay: effective unit/subunits come from the NR's
            # 1:1 tagging entries where present (as in the attendance view).
            entry_by_person: dict[str, TaggingEntry] = {}
            tagging = await _load_nr_tagging(db, nr_id, with_entries=False)
            if tagging is not None:
                entries = (
                    await db.execute(
                        select(TaggingEntry).where(
                            TaggingEntry.tagging_id == str(tagging.id)
                        )
                    )
                ).scalars().all()
                entry_by_person = {str(e.personnel_id): e for e in entries}

            attendance_rows = (
                await db.execute(
                    select(Attendance).where(
                        Attendance.nominal_roll_id == nr_id,
                        Attendance.date == target_date,
                    )
                )
            ).scalars().all()
            att_by_person = {a.personnel_id: a for a in attendance_rows}

            # (effective sub_unit_1, effective sub_unit_2, category, slot
            # status) per person; no attendance row = absent (model default).
            per_person: list[tuple[str | None, str | None, str, str]] = []
            for person in roster:
                entry = entry_by_person.get(str(person.id))
                record = att_by_person.get(str(person.id))
                status = (
                    record.status_pm if slot == "pm" else record.status_am
                ) if record is not None else "absent"
                per_person.append(
                    (
                        entry.to_sub_unit_1 if entry is not None else person.sub_unit_1,
                        entry.to_sub_unit_2 if entry is not None else person.sub_unit_2,
                        person.category,
                        status,
                    )
                )

            # Subunit-1 access scope (deny-by-default, tagging-aware) —
            # super-admins bypass and see the whole unit.
            if current_admin.role != "super_admin":
                allowed = await get_assigned_subunit_1s(
                    db, str(current_admin.id), nr_id
                )
                no_assignments = not allowed
                per_person = [t for t in per_person if t[0] in allowed]

            # Aggregate into (sub_unit_1, sub_unit_2) cells, then section
            # per sub_unit_1 (displayed once) with a SUBTOTAL, plus a
            # unit-wide TOTAL rollup.
            cells: dict[tuple[str | None, str | None], dict] = {}
            for sub1, sub2, category, status in per_person:
                buckets = cells.setdefault((sub1, sub2), _strength_buckets())
                bucket = buckets[category]
                bucket["in"] += 1
                if status in PRESENT_LIKE_STATUSES:
                    bucket["current"] += 1

            grand = _strength_buckets()
            ordered = sorted(
                cells.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")
            )
            for (sub1, sub2), buckets in ordered:
                if not sections or sections[-1]["name"] != (sub1 or "(none)"):
                    sections.append(
                        {
                            "name": sub1 or "(none)",
                            "rows": [],
                            "_buckets": _strength_buckets(),
                        }
                    )
                section = sections[-1]
                section["rows"].append(
                    {"name": sub2 or "(none)", "cells": _strength_cells(buckets)}
                )
                for category in ("Officer", "WOSE"):
                    for key in ("in", "current"):
                        value = buckets[category][key]
                        section["_buckets"][category][key] += value
                        grand[category][key] += value

            for section in sections:
                section["subtotal"] = _strength_cells(section.pop("_buckets"))
            total_cells = _strength_cells(grand)

    env = get_templates(request)
    template = env.get_template("admin/unit_strength.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="strength",
        nr_label=nr_label,
        target_date=target_date,
        slot=slot,
        sections=sections,
        total=total_cells,
        no_assignments=no_assignments,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    search: str | None = None,
    status_filter: str | None = None,
    role_filter: str | None = None,
):
    """Render the users management page."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Build query with optional filters
    session_maker = get_session_maker()
    async with session_maker() as db:
        query = select(User, AccessLevel).outerjoin(
            AccessLevel, User.access_level_id == AccessLevel.id
        )

        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    User.email.ilike(search_pattern),
                    User.name.ilike(search_pattern),
                )
            )

        if status_filter:
            query = query.where(User.status == status_filter)

        if role_filter:
            query = query.where(User.role == role_filter)

        query = query.order_by(User.created_at.desc())

        result = await db.execute(query)
        rows = result.all()

    users = [
        {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "status": user.status,
            "access_level": access_level.name if access_level else None,
            "created_at": user.created_at,
            "last_sign_in_at": user.last_sign_in_at,
        }
        for user, access_level in rows
    ]

    env = get_templates(request)
    template = env.get_template("admin/users.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="users",
        users=users,
        search=search or "",
        status_filter=status_filter or "",
        role_filter=role_filter or "",
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/csv-upload", response_class=HTMLResponse)
async def admin_csv_upload(
    request: Request,
):
    """Render the CSV upload page."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    # Fetch recent uploads for the table
    session_maker = get_session_maker()
    async with session_maker() as db:
        recent_uploads_rows = (
            await db.execute(
                select(
                    CsvUpload.id,
                    CsvUpload.sha256_hash,
                    CsvUpload.line_count,
                    CsvUpload.status,
                    CsvUpload.uploaded_at,
                    CsvUpload.nominal_roll_id,
                    CsvUpload.original_filename,
                )
                .order_by(CsvUpload.uploaded_at.desc())
                .limit(20)
            )
        ).all()

        # Processable uploads (no NR yet) + source NRs (have at least one
        # tagging entry). Both feed the Step 2 form.
        processable_uploads_rows = (
            await db.execute(
                select(
                    CsvUpload.id,
                    CsvUpload.original_filename,
                    CsvUpload.line_count,
                    CsvUpload.uploaded_at,
                )
                .where(CsvUpload.nominal_roll_id.is_(None))
                .order_by(CsvUpload.uploaded_at.desc())
                .limit(20)
            )
        ).all()

        source_nr_rows = (
            await db.execute(
                select(
                    NominalRoll.id,
                    NominalRoll.caa,
                    func.count(TaggingEntry.id).label("entry_count"),
                )
                .join(Tagging, Tagging.nominal_roll_id == NominalRoll.id)
                .outerjoin(TaggingEntry, TaggingEntry.tagging_id == Tagging.id)
                .group_by(NominalRoll.id, NominalRoll.caa)
                .having(func.count(TaggingEntry.id) > 0)
                .order_by(NominalRoll.caa.desc())
            )
        ).all()

    recent_uploads = [
        {
            "id": row.id,
            "sha256_hash": row.sha256_hash[:12] + "...",
            "line_count": row.line_count,
            "status": row.status,
            "uploaded_at": row.uploaded_at,
            "nominal_roll_id": row.nominal_roll_id,
            "original_filename": row.original_filename,
        }
        for row in recent_uploads_rows
    ]

    processable_uploads = [
        {
            "id": row.id,
            "original_filename": row.original_filename or "(no filename)",
            "line_count": row.line_count,
            "uploaded_at": row.uploaded_at,
        }
        for row in processable_uploads_rows
    ]

    source_nominal_rolls = [
        {"id": row.id, "caa": row.caa, "entry_count": row.entry_count}
        for row in source_nr_rows
    ]

    env = get_templates(request)
    template = env.get_template("admin/csv_upload.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="csv-upload",
        recent_uploads=recent_uploads,
        processable_uploads=processable_uploads,
        source_nominal_rolls=source_nominal_rolls,
    )

    return HTMLResponse(content=html_content)


@router.get(
    "/admin/deferments",
    response_class=HTMLResponse,
    dependencies=[Depends(require_feature("FEATURE_DEFERMENTS"))],
)
async def admin_deferments(
    request: Request,
    status_filter: str | None = None,
    nominal_roll_id: str | None = None,
):
    """Render the deferments management page (super-admin only)."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)
    if current_admin.role != "super_admin":
        return _no_permission_response(request, current_admin, "Deferments", "deferments")

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Nominal roll list for the selector (most recent first).
        roll_rows = (
            await db.execute(
                select(NominalRoll.id, NominalRoll.caa)
                .order_by(NominalRoll.caa.desc())
                .limit(50)
            )
        ).all()
        nominal_roll_options = [
            {
                "id": str(row.id),
                "caa": row.caa.isoformat() if row.caa is not None else str(row.id)[:8],
            }
            for row in roll_rows
        ]

        # Page always scopes to exactly one nominal roll. If none given, default
        # to the most recent roll (first in the dropdown).
        resolved_nominal_roll_id = nominal_roll_id or (
            nominal_roll_options[0]["id"] if nominal_roll_options else None
        )

        query = (
            select(
                Deferment,
                Personnel.nominal_roll_id,
                NominalRoll.caa,
            )
            .join(Personnel, Deferment.personnel_id == Personnel.id)
            .outerjoin(NominalRoll, Personnel.nominal_roll_id == NominalRoll.id)
            .order_by(Deferment.created_at.desc())
            .limit(200)
        )
        if status_filter:
            query = query.where(Deferment.status == status_filter)
        if resolved_nominal_roll_id:
            query = query.where(Personnel.nominal_roll_id == resolved_nominal_roll_id)

        rows = (await db.execute(query)).all()

    deferments_data = [
        {
            "id": str(d.id),
            "personnel_id": d.personnel_id,
            "nominal_roll_id": eid,
            "nominal_roll_caa": caa.isoformat() if caa else None,
            "rank_name": d.rank_name,
            "sub_unit": d.sub_unit or "—",
            "reason": d.reason,
            "status": d.status,
            "remarks": d.remarks or "",
            "oc_updates": d.oc_updates or "",
            "created_at": d.created_at,
            "updated_at": d.updated_at,
        }
        for d, eid, caa in rows
    ]

    env = get_templates(request)
    template = env.get_template("admin/deferments.html")

    active_nominal_roll_caa = next(
        (e["caa"] for e in nominal_roll_options if e["id"] == resolved_nominal_roll_id),
        "—",
    )

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="deferments",
        deferments=deferments_data,
        nominal_roll_options=nominal_roll_options,
        status_filter=status_filter or "",
        nominal_roll_filter=resolved_nominal_roll_id or "",
        active_nominal_roll_caa=active_nominal_roll_caa,
        deferment_statuses=DEFERMENT_STATUSES,
        deferment_reasons=DEFERMENT_REASONS,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/taggings", response_class=HTMLResponse)
async def admin_taggings(
    request: Request,
    nominal_roll_id: str | None = None,
):
    """Render the tagging overlay management page (super-admin only).

    Under the 1:1 model, each NR has exactly one tagging. This page shows
    the entries of the selected NR's tagging (the remap list) and offers
    edit/clone-into actions.
    """
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)
    if current_admin.role != "super_admin":
        return _no_permission_response(request, current_admin, "Taggings", "taggings")

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Nominal roll list for the selector (most recent first).
        roll_rows = (
            await db.execute(
                select(NominalRoll.id, NominalRoll.caa)
                .order_by(NominalRoll.caa.desc())
                .limit(50)
            )
        ).all()
        nominal_roll_options = [
            {
                "id": str(row.id),
                "caa": row.caa.isoformat() if row.caa is not None else str(row.id)[:8],
            }
            for row in roll_rows
        ]

        # Page always scopes to exactly one nominal roll. Default to most recent.
        resolved_nominal_roll_id = nominal_roll_id or (
            nominal_roll_options[0]["id"] if nominal_roll_options else None
        )

        # The selected NR's single tagging (1:1) + entries joined to Personnel.
        tagging_data = None
        entries_data: list[dict] = []
        if resolved_nominal_roll_id:
            tagging_row = (
                await db.execute(
                    select(Tagging).where(
                        Tagging.nominal_roll_id == resolved_nominal_roll_id
                    )
                )
            ).scalar_one_or_none()

            if tagging_row is not None:
                entry_rows = (
                    await db.execute(
                        select(
                            TaggingEntry.id,
                            TaggingEntry.personnel_id,
                            Personnel.pers_no,
                            Personnel.rank,
                            Personnel.full_name,
                            TaggingEntry.from_unit,
                            TaggingEntry.from_sub_unit_1,
                            TaggingEntry.from_sub_unit_2,
                            TaggingEntry.from_sub_unit_3,
                            TaggingEntry.to_unit,
                            TaggingEntry.to_sub_unit_1,
                            TaggingEntry.to_sub_unit_2,
                            TaggingEntry.to_sub_unit_3,
                        )
                        .outerjoin(Personnel, Personnel.id == TaggingEntry.personnel_id)
                        .where(TaggingEntry.tagging_id == tagging_row.id)
                        .order_by(Personnel.rank, Personnel.full_name)
                    )
                ).all()

                entries_data = [
                    {
                        "id": str(row.id),
                        "personnel_id": row.personnel_id,
                        "pers_no": row.pers_no,
                        "label": f"{row.rank} {row.full_name}".strip()
                        if row.rank
                        else "(unknown)",
                        "from_unit": row.from_unit,
                        "from_sub_unit_1": row.from_sub_unit_1,
                        "from_sub_unit_2": row.from_sub_unit_2,
                        "from_sub_unit_3": row.from_sub_unit_3,
                        "to_unit": row.to_unit,
                        "to_sub_unit_1": row.to_sub_unit_1,
                        "to_sub_unit_2": row.to_sub_unit_2,
                        "to_sub_unit_3": row.to_sub_unit_3,
                    }
                    for row in entry_rows
                ]

                tagging_data = {
                    "id": str(tagging_row.id),
                    "label": tagging_row.label,
                    "remarks": tagging_row.remarks or "",
                    "entry_count": len(entries_data),
                    "created_at": tagging_row.created_at,
                    "updated_at": tagging_row.updated_at,
                }

    env = get_templates(request)
    template = env.get_template("admin/taggings.html")

    active_nominal_roll_caa = next(
        (e["caa"] for e in nominal_roll_options if e["id"] == resolved_nominal_roll_id),
        "—",
    )

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="taggings",
        tagging=tagging_data,
        entries=entries_data,
        nominal_roll_options=nominal_roll_options,
        nominal_roll_filter=resolved_nominal_roll_id or "",
        active_nominal_roll_caa=active_nominal_roll_caa,
    )

    return HTMLResponse(content=html_content)



@router.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings(
    request: Request,
):
    """Render the settings page."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    from parade_state.config import get_settings

    env = get_templates(request)
    template = env.get_template("admin/settings.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="settings",
        purge_enabled=get_settings().PURGE_ENABLED,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit(
    request: Request,
    entity_type: str | None = None,
    action: str | None = None,
    target_user_id: str | None = None,
    page: int = 1,
):
    """Render the audit log page with filtering and pagination."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    limit = 50
    offset = (page - 1) * limit

    session_maker = get_session_maker()
    async with session_maker() as db:
        conditions = []
        if entity_type:
            conditions.append(AuditLog.entity_type == entity_type)
        if action:
            conditions.append(AuditLog.action == action)
        if target_user_id:
            conditions.append(AuditLog.user_id == target_user_id)

        data_query = (
            select(AuditLog, User)
            .join(User, AuditLog.user_id == User.id, isouter=True)
            .order_by(AuditLog.timestamp.desc())
            .offset(offset)
            .limit(limit)
        )
        for cond in conditions:
            data_query = data_query.where(cond)

        rows = (await db.execute(data_query)).all()

        count_query = select(func.count()).select_from(AuditLog)
        for cond in conditions:
            count_query = count_query.where(cond)
        total = (await db.execute(count_query)).scalar_one()

    logs = [
        {
            "timestamp": log.timestamp,
            "user_name": user_obj.name if user_obj else "System",
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "action": log.action,
            "description": log.description,
            "ip_address": log.ip_address,
        }
        for log, user_obj in rows
    ]

    total_pages = max(1, (total + limit - 1) // limit)

    env = get_templates(request)
    template = env.get_template("admin/audit.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="audit",
        logs=logs,
        entity_type=entity_type or "",
        action=action or "",
        target_user_id=target_user_id or "",
        page=page,
        total_pages=total_pages,
        total=total,
        entity_types=AUDIT_ENTITY_TYPES,
        actions=AUDIT_ACTIONS,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/database-restore", response_class=HTMLResponse)
async def admin_database_restore(request: Request):
    """Render the database restore page (super-admin only)."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)
    if current_admin.role != "super_admin":
        return _no_permission_response(
            request, current_admin, "Restore Backup", "database-restore"
        )

    from parade_state.config import get_settings

    settings = get_settings()
    database_name = (
        urlsplit(settings.DATABASE_URL).path.lstrip("/") or "postgres"
    )

    env = get_templates(request)
    template = env.get_template("admin/db_restore.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="database-restore",
        database_name=database_name,
        restore_enabled=settings.RESTORE_ENABLED,
    )

    return HTMLResponse(content=html_content)
