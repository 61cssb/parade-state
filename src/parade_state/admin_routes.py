"""Admin interface routes using Jinja2 templates."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, or_, select

from parade_state.auth.admin_dependencies import (
    get_current_admin_user_optional,
    require_admin_user_flexible,
)
from parade_state.db import get_session_maker
from parade_state.models import (
    AccessLevel,
    AuditLog,
    CsvUpload,
    Deployment,
    DeploymentPersonnelExclusion,
    Estab,
    Personnel,
    User,
)
from parade_state.models import (
    Session as SessionModel,
)

router = APIRouter()
depends_admin = Depends(require_admin_user_flexible)

# Audit log filter dropdown options (mirrors AuditLog model enum values)
AUDIT_ENTITY_TYPES = [
    "attendance",
    "deployment",
    "session",
    "user",
    "csv_upload",
    "estab",
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

        open_sessions = (
            await db.execute(
                select(func.count(SessionModel.id)).where(SessionModel.status == "open")
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
        open_sessions=open_sessions,
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
    """Render the deployments management page with expandable session sub-views."""
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

        # Batch-load sessions for all deployments on the page
        sessions_by_deployment: dict[str, list] = {}
        if deployments:
            deployment_ids = [str(d.id) for d in deployments]
            sessions_result = await db.execute(
                select(SessionModel)
                .where(SessionModel.deployment_id.in_(deployment_ids))
                .order_by(SessionModel.date.desc(), SessionModel.created_at.desc())
            )
            for session in sessions_result.scalars().all():
                sessions_by_deployment.setdefault(
                    str(session.deployment_id), []
                ).append(session)

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

    sessions_data = {
        dep_id: [
            {
                "id": str(s.id),
                "deployment_id": str(s.deployment_id),
                "date": s.date,
                "session_type": s.session_type,
                "status": s.status,
            }
            for s in sessions
        ]
        for dep_id, sessions in sessions_by_deployment.items()
    }

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
        sessions_by_deployment=sessions_data,
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

        # Fetch all estab personnel
        personnel_query = (
            select(Personnel)
            .where(Personnel.estab_id == deployment.estab_id)
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
                    CsvUpload.estab_id,
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
            "estab_id": row.estab_id,
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


@router.get("/admin/estabs", response_class=HTMLResponse)
async def admin_estabs(
    request: Request,
    status_filter: str | None = None,
):
    """Render the estabs management page."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    session_maker = get_session_maker()
    async with session_maker() as db:
        # Most recent CsvUpload per estab (provides original_filename).
        latest_upload_subq = (
            select(
                CsvUpload.estab_id.label("estab_id"),
                CsvUpload.original_filename.label("original_filename"),
            )
            .where(CsvUpload.estab_id.is_not(None))
            .order_by(CsvUpload.estab_id, CsvUpload.uploaded_at.desc())
            .subquery()
        )

        query = (
            select(
                Estab.id,
                Estab.caa,
                Estab.status,
                Estab.personnel_count,
                Estab.uploaded_at,
                Estab.csv_hash,
                latest_upload_subq.c.original_filename,
            )
            .outerjoin(
                latest_upload_subq,
                latest_upload_subq.c.estab_id == Estab.id,
            )
            .order_by(Estab.uploaded_at.desc())
            .limit(100)
        )
        if status_filter:
            query = query.where(Estab.status == status_filter)

        rows = (await db.execute(query)).all()

    estabs = [
        {
            "id": str(row.id),
            "caa": row.caa,
            "status": row.status,
            "personnel_count": row.personnel_count,
            "uploaded_at": row.uploaded_at,
            "csv_hash": row.csv_hash[:12] + "...",
            "original_filename": row.original_filename or "—",
        }
        for row in rows
    ]

    env = get_templates(request)
    template = env.get_template("admin/estabs.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="estabs",
        estabs=estabs,
        status_filter=status_filter or "",
        estab_statuses=["draft", "confirmed", "archived"],
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
