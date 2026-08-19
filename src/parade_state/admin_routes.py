"""Admin interface routes using Jinja2 templates."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import and_, func, or_, select
from urllib.parse import urlsplit

from parade_state.auth.admin_dependencies import (
    get_current_admin_user_optional,
    require_admin_user_flexible,
)
from parade_state.db import get_session_maker
from parade_state.models import (
    AccessLevel,
    Attendance,
    AuditLog,
    CsvUpload,
    Deferment,
    Grouping,
    GroupingPersonnelExclusion,
    NominalRoll,
    Personnel,
    Tagging,
    TaggingEntry,
    User,
    UserSubunitAssignment,
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

# Grouping status filter dropdown options (mirrors Grouping model enum)
GROUPING_STATUSES = [
    "draft",
    "active",
    "inactive",
    "archived",
    "closed",
    "finalized",
]

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


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
):
    """Render the admin dashboard page."""
    # Check if user is authenticated
    current_admin = await get_current_admin_user_optional(request)

    if not current_admin:
        # Redirect to login if not authenticated
        return RedirectResponse(url="/auth/login", status_code=302)

    # Fetch real statistics from the database
    session_maker = get_session_maker()
    async with session_maker() as db:
        active_groupings = (
            await db.execute(
                select(func.count(Grouping.id)).where(Grouping.status == "active")
            )
        ).scalar_one()

        active_attendance_nrs = (
            await db.execute(
                select(func.count(NominalRoll.id)).where(
                    NominalRoll.attendance_active.is_(True)
                )
            )
        ).scalar_one()

        total_personnel = (
            await db.execute(
                select(func.count(Personnel.id)).where(Personnel.status == "active")
            )
        ).scalar_one()

        active_users = (
            await db.execute(
                select(func.count(User.id)).where(User.status == "active")
            )
        ).scalar_one()

        recent_activity_rows = (
            await db.execute(
                select(AuditLog, User)
                .join(User, AuditLog.user_id == User.id, isouter=True)
                .order_by(AuditLog.timestamp.desc())
                .limit(10)
            )
        ).all()

    recent_activity = [
        {
            "timestamp": log.timestamp,
            "user_name": user_obj.name if user_obj else "System",
            "action": log.action,
            "entity_type": log.entity_type,
            "description": log.description,
        }
        for log, user_obj in recent_activity_rows
    ]

    env = get_templates(request)
    template = env.get_template("admin/dashboard.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="dashboard",
        active_groupings=active_groupings,
        active_scopes=active_attendance_nrs,
        total_personnel=total_personnel,
        active_users=active_users,
        recent_activity=recent_activity,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/groupings", response_class=HTMLResponse)
async def admin_groupings(
    request: Request,
    status_filter: str | None = None,
):
    """Render the groupings management page.

    Sessions (AM/PM) are now hardcoded and no longer user-managed — the
    expanded session sub-views and create-session form have been removed.
    Attendance is managed from the (forthcoming) attendance admin page.
    """
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        query = select(Grouping).order_by(Grouping.created_at.desc()).limit(100)
        if status_filter:
            query = query.where(Grouping.status == status_filter)

        groupings_result = await db.execute(query)
        groupings = groupings_result.scalars().all()

    groupings_data = [
        {
            "id": str(g.id),
            "name": g.name,
            "mode": g.mode,
            "status": g.status,
            "valid_from": g.valid_from,
            "valid_until": g.valid_until,
            "notes": g.notes,
            "created_at": g.created_at,
        }
        for g in groupings
    ]

    env = get_templates(request)
    template = env.get_template("admin/groupings.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="groupings",
        groupings=groupings_data,
        status_filter=status_filter or "",
        grouping_statuses=GROUPING_STATUSES,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/groupings/{grouping_id}/personnel", response_class=HTMLResponse)
async def admin_grouping_personnel(
    request: Request,
    grouping_id: str,
):
    """Render the grouping personnel management page (included/excluded lists)."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Fetch grouping
        result = await db.execute(
            select(Grouping).where(Grouping.id == grouping_id)
        )
        grouping = result.scalar_one_or_none()
        if not grouping:
            return RedirectResponse(url="/admin/groupings", status_code=302)

        # Fetch all personnel from the grouping's nominal roll
        personnel_query = (
            select(Personnel)
            .where(Personnel.nominal_roll_id == grouping.nominal_roll_id)
            .order_by(Personnel.unit, Personnel.sub_unit_1, Personnel.rank, Personnel.full_name)
        )
        personnel_result = await db.execute(personnel_query)
        all_personnel = personnel_result.scalars().all()

        # Fetch exclusion records for this grouping
        exclusions_result = await db.execute(
            select(GroupingPersonnelExclusion).where(
                GroupingPersonnelExclusion.grouping_id == grouping_id
            )
        )
        exclusions = exclusions_result.scalars().all()
        excluded_map = {str(e.personnel_id) for e in exclusions}

    # Build unified list with is_excluded flag
    personnel_rows = []
    included_count = 0
    excluded_count = 0
    for p in all_personnel:
        is_excluded = str(p.id) in excluded_map
        if is_excluded:
            excluded_count += 1
        else:
            included_count += 1
        personnel_rows.append({
            "id": str(p.id),
            "pers_no": p.pers_no,
            "rank": p.rank,
            "category": p.category,
            "full_name": p.full_name,
            "unit": p.unit,
            "sub_unit_1": p.sub_unit_1,
            "is_excluded": is_excluded,
        })

    env = get_templates(request)
    template = env.get_template("admin/grouping_personnel.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="groupings",
        grouping={
            "id": str(grouping.id),
            "name": grouping.name,
            "status": grouping.status,
        },
        is_draft=grouping.status == "draft",
        personnel_rows=personnel_rows,
        included_count=included_count,
        excluded_count=excluded_count,
        total_count=len(all_personnel),
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions(request: Request):
    """Redirect to combined groupings page (sessions managed there)."""
    return RedirectResponse(url="/admin/groupings", status_code=302)


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


@router.get("/admin/nominal-rolls", response_class=HTMLResponse)
async def admin_nominal_rolls(
    request: Request,
):
    """Render the nominal rolls management page.

    Highlights the NR currently active for attendance; super-admins can
    toggle "Use for Attendance" / "Deactivate Attendance" per row.
    """
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Most recent CsvUpload per nominal roll (provides original_filename).
        latest_upload_subq = (
            select(
                CsvUpload.nominal_roll_id.label("nominal_roll_id"),
                CsvUpload.original_filename.label("original_filename"),
            )
            .where(CsvUpload.nominal_roll_id.is_not(None))
            .order_by(CsvUpload.nominal_roll_id, CsvUpload.uploaded_at.desc())
            .subquery()
        )

        query = (
            select(
                NominalRoll.id,
                NominalRoll.caa,
                NominalRoll.attendance_active,
                NominalRoll.attendance_activated_at,
                NominalRoll.personnel_count,
                NominalRoll.uploaded_at,
                NominalRoll.csv_hash,
                NominalRoll.label,
                NominalRoll.remarks,
                latest_upload_subq.c.original_filename,
            )
            .outerjoin(
                latest_upload_subq,
                latest_upload_subq.c.nominal_roll_id == NominalRoll.id,
            )
            .order_by(NominalRoll.uploaded_at.desc())
            .limit(100)
        )

        rows = (await db.execute(query)).all()

    nominal_rolls = [
        {
            "id": str(row.id),
            "caa": row.caa,
            "attendance_active": bool(row.attendance_active),
            "attendance_activated_at": row.attendance_activated_at,
            "personnel_count": row.personnel_count,
            "uploaded_at": row.uploaded_at,
            "csv_hash": row.csv_hash[:12] + "...",
            "original_filename": row.original_filename or "—",
            "label": row.label,
            "remarks": row.remarks,
        }
        for row in rows
    ]

    env = get_templates(request)
    template = env.get_template("admin/nominal_rolls.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="nominal-rolls",
        nominal_rolls=nominal_rolls,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/deferments", response_class=HTMLResponse)
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
        return RedirectResponse(url="/admin", status_code=302)

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
        return RedirectResponse(url="/admin", status_code=302)

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
        return RedirectResponse(url="/admin", status_code=302)

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
