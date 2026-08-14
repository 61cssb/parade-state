"""Admin interface routes using Jinja2 templates."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import and_, func, or_, select

from parade_state.auth.admin_dependencies import (
    get_current_admin_user_optional,
    require_admin_user_flexible,
)
from parade_state.db import get_session_maker
from parade_state.models import (
    AccessLevel,
    Attendance,
    AttendanceScope,
    AuditLog,
    CsvUpload,
    Deferment,
    Deployment,
    DeploymentPersonnelExclusion,
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
    "deployment",
    "session",
    "user",
    "csv_upload",
    "nominal_roll",
    "personnel",
    "access_level",
    "column_mapping",
]
AUDIT_ACTIONS = ["create", "update", "delete", "archive", "close", "finalize"]

# Deployment status filter dropdown options (mirrors Deployment model enum)
DEPLOYMENT_STATUSES = [
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
        active_deployments = (
            await db.execute(
                select(func.count(Deployment.id)).where(Deployment.status == "active")
            )
        ).scalar_one()

        active_scopes = (
            await db.execute(
                select(func.count(AttendanceScope.nominal_roll_id))
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
        active_deployments=active_deployments,
        active_scopes=active_scopes,
        total_personnel=total_personnel,
        active_users=active_users,
        recent_activity=recent_activity,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/deployments", response_class=HTMLResponse)
async def admin_deployments(
    request: Request,
    status_filter: str | None = None,
):
    """Render the deployments management page.

    Sessions (AM/PM) are now hardcoded and no longer user-managed — the
    expanded session sub-views and create-session form have been removed.
    Attendance is managed from the (forthcoming) attendance admin page.
    """
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        query = select(Deployment).order_by(Deployment.created_at.desc()).limit(100)
        if status_filter:
            query = query.where(Deployment.status == status_filter)

        deployments_result = await db.execute(query)
        deployments = deployments_result.scalars().all()

    deployments_data = [
        {
            "id": str(d.id),
            "name": d.name,
            "status": d.status,
            "valid_from": d.valid_from,
            "valid_until": d.valid_until,
            "notes": d.notes,
            "created_at": d.created_at,
        }
        for d in deployments
    ]

    env = get_templates(request)
    template = env.get_template("admin/deployments.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="deployments",
        deployments=deployments_data,
        status_filter=status_filter or "",
        deployment_statuses=DEPLOYMENT_STATUSES,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/deployments/{deployment_id}/personnel", response_class=HTMLResponse)
async def admin_deployment_personnel(
    request: Request,
    deployment_id: str,
):
    """Render the deployment personnel management page (included/excluded lists)."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Fetch deployment
        result = await db.execute(
            select(Deployment).where(Deployment.id == deployment_id)
        )
        deployment = result.scalar_one_or_none()
        if not deployment:
            return RedirectResponse(url="/admin/deployments", status_code=302)

        # Fetch all personnel from the deployment's nominal roll
        personnel_query = (
            select(Personnel)
            .where(Personnel.nominal_roll_id == deployment.nominal_roll_id)
            .order_by(Personnel.unit, Personnel.sub_unit_1, Personnel.rank, Personnel.full_name)
        )
        personnel_result = await db.execute(personnel_query)
        all_personnel = personnel_result.scalars().all()

        # Fetch exclusion records for this deployment
        exclusions_result = await db.execute(
            select(DeploymentPersonnelExclusion).where(
                DeploymentPersonnelExclusion.deployment_id == deployment_id
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
            "short_id": p.short_id,
            "rank": p.rank,
            "category": p.category,
            "full_name": p.full_name,
            "unit": p.unit,
            "sub_unit_1": p.sub_unit_1,
            "is_excluded": is_excluded,
        })

    env = get_templates(request)
    template = env.get_template("admin/deployment_personnel.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="deployments",
        deployment={
            "id": str(deployment.id),
            "name": deployment.name,
            "status": deployment.status,
        },
        is_draft=deployment.status == "draft",
        personnel_rows=personnel_rows,
        included_count=included_count,
        excluded_count=excluded_count,
        total_count=len(all_personnel),
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions(request: Request):
    """Redirect to combined deployments page (sessions managed there)."""
    return RedirectResponse(url="/admin/deployments", status_code=302)


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
                )
                .order_by(CsvUpload.uploaded_at.desc())
                .limit(20)
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
        }
        for row in recent_uploads_rows
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
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/nominal-rolls", response_class=HTMLResponse)
async def admin_nominal_rolls(
    request: Request,
    status_filter: str | None = None,
):
    """Render the nominal rolls management page."""
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
                NominalRoll.status,
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
        if status_filter:
            query = query.where(NominalRoll.status == status_filter)

        rows = (await db.execute(query)).all()

    nominal_rolls = [
        {
            "id": str(row.id),
            "caa": row.caa,
            "status": row.status,
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
        status_filter=status_filter or "",
        nominal_roll_statuses=["draft", "confirmed", "archived"],
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
    """Render the tagging overlay management page (super-admin only)."""
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

        # Taggings for the selected NR with entry counts (correlated subquery).
        entry_count = (
            select(func.count())
            .select_from(TaggingEntry)
            .where(TaggingEntry.tagging_id == Tagging.id)
            .correlate(Tagging)
            .scalar_subquery()
        )
        query = (
            select(Tagging, entry_count, NominalRoll.caa)
            .outerjoin(NominalRoll, Tagging.nominal_roll_id == NominalRoll.id)
            .order_by(Tagging.created_at.desc())
            .limit(200)
        )
        if resolved_nominal_roll_id:
            query = query.where(Tagging.nominal_roll_id == resolved_nominal_roll_id)

        rows = (await db.execute(query)).all()

    taggings_data = [
        {
            "id": str(t.id),
            "label": t.label,
            "nominal_roll_id": t.nominal_roll_id,
            "nominal_roll_caa": caa.isoformat() if caa else None,
            "remarks": t.remarks or "",
            "entry_count": count or 0,
            "created_at": t.created_at,
            "updated_at": t.updated_at,
        }
        for t, count, caa in rows
    ]

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
        taggings=taggings_data,
        nominal_roll_options=nominal_roll_options,
        nominal_roll_filter=resolved_nominal_roll_id or "",
        active_nominal_roll_caa=active_nominal_roll_caa,
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/attendance", response_class=HTMLResponse)
async def admin_attendance(
    request: Request,
    nominal_roll_id: str | None = None,
    date: utc_dt.date | None = None,
    sub_unit_1: str | None = None,
):
    """Render the admin attendance page (super-admin only).

    Super-admin control center: activate the NR's attendance scope (NR itself
    or a Tagging), filter the roster by sub_unit_1, edit AM/PM status +
    remarks, and trigger Copy Remarks. Copy Remarks is disabled on the NR's
    first day (no prior attendance).
    """
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)
    if current_admin.role != "super_admin":
        return RedirectResponse(url="/admin", status_code=302)

    target_date = date or utc_dt.utcnow().date()

    session_maker = get_session_maker()
    async with session_maker() as db:
        # NR selector options (most recent first).
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
                "caa": row.caa.isoformat() if row.caa else str(row.id)[:8],
            }
            for row in roll_rows
        ]
        resolved_nr_id = nominal_roll_id or (
            nominal_roll_options[0]["id"] if nominal_roll_options else None
        )

        active_scope = None
        scope_taggings = []
        subunit_options: list[str] = []
        roster_rows: list[dict] = []
        has_prior_attendance = False
        counts = {"am": {"present": 0, "absent": 0, "total": 0},
                  "pm": {"present": 0, "absent": 0, "total": 0}}

        if resolved_nr_id:
            # Active scope for this NR.
            scope_result = await db.execute(
                select(AttendanceScope).where(
                    AttendanceScope.nominal_roll_id == resolved_nr_id
                )
            )
            active_scope = scope_result.scalar_one_or_none()

            # Taggings on this NR (for the scope-activation dropdown).
            tagging_rows = (
                await db.execute(
                    select(Tagging).where(
                        Tagging.nominal_roll_id == resolved_nr_id
                    ).order_by(Tagging.label)
                )
            ).scalars().all()
            scope_taggings = [
                {"id": str(t.id), "label": t.label} for t in tagging_rows
            ]

            # Distinct sub_unit_1 values on the NR (for the filter dropdown).
            sub_rows = (
                await db.execute(
                    select(Personnel.sub_unit_1)
                    .where(
                        Personnel.nominal_roll_id == resolved_nr_id,
                        Personnel.status == "active",
                        Personnel.sub_unit_1.is_not(None),
                    )
                    .distinct()
                    .order_by(Personnel.sub_unit_1)
                )
            ).all()
            subunit_options = [r[0] for r in sub_rows if r[0]]

            # Roster (optionally filtered by sub_unit_1).
            roster_query = (
                select(Personnel).where(
                    Personnel.nominal_roll_id == resolved_nr_id,
                    Personnel.status == "active",
                ).order_by(
                    Personnel.unit,
                    Personnel.sub_unit_1,
                    Personnel.rank,
                    Personnel.full_name,
                )
            )
            if sub_unit_1:
                roster_query = roster_query.where(
                    Personnel.sub_unit_1 == sub_unit_1
                )
            roster = (await db.execute(roster_query)).scalars().all()

            # Today's attendance for these personnel.
            att_rows = (
                await db.execute(
                    select(Attendance).where(
                        Attendance.nominal_roll_id == resolved_nr_id,
                        Attendance.date == target_date,
                    )
                )
            ).scalars().all()
            att_by_pid = {a.personnel_id: a for a in att_rows}

            from parade_state.models.attendance import PRESENT_LIKE_STATUSES

            for person in roster:
                record = att_by_pid.get(str(person.id))
                for slot, status_val in (
                    ("am", record.status_am if record else "absent"),
                    ("pm", record.status_pm if record else "absent"),
                ):
                    counts[slot]["total"] += 1
                    if status_val in PRESENT_LIKE_STATUSES:
                        counts[slot]["present"] += 1
                    else:
                        counts[slot]["absent"] += 1
                roster_rows.append({
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
                })

            # Has any attendance before today? (drives Copy Remarks day-1 disable)
            has_prior_attendance = (
                await db.execute(
                    select(func.count())
                    .select_from(Attendance)
                    .where(
                        Attendance.nominal_roll_id == resolved_nr_id,
                        Attendance.date < target_date,
                    )
                )
            ).scalar_one() > 0

    env = get_templates(request)
    template = env.get_template("admin/attendance.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="attendance-admin",
        nominal_roll_options=nominal_roll_options,
        nominal_roll_filter=resolved_nr_id or "",
        target_date=target_date,
        sub_unit_1_filter=sub_unit_1 or "",
        subunit_options=subunit_options,
        active_scope=(
            {
                "tagging_id": active_scope.tagging_id,
                "activated_at": active_scope.activated_at,
            }
            if active_scope
            else None
        ),
        scope_taggings=scope_taggings,
        roster_rows=roster_rows,
        has_prior_attendance=has_prior_attendance,
        counts=counts,
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
