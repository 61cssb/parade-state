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
    Personnel,
    User,
)
from parade_state.models import (
    Session as SessionModel,
)

router = APIRouter()
depends_admin = Depends(require_admin_user_flexible)

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
):
    """Render the deployments management page."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

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
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions(
    request: Request,
):
    """Render the sessions management page."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    env = get_templates(request)
    template = env.get_template("admin/sessions.html")

    html_content = template.render(
        request=request,
        user={
            "id": current_admin.id,
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role,
        },
        active_page="sessions",
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
):
    """Render the audit log page."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

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
    )

    return HTMLResponse(content=html_content)
