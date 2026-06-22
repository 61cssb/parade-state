"""Admin interface routes using Jinja2 templates."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

from parade_state.auth.admin_dependencies import get_current_admin_user_optional, require_admin_user_flexible
from parade_state.db import get_db_session
from parade_state.models import User

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

    env = get_templates(request)
    template = env.get_template("admin/dashboard.html")

    html_content = template.render(
        request=request,
        user={
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role
        },
        active_page="dashboard",
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
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role
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
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role
        },
        active_page="sessions",
    )

    return HTMLResponse(content=html_content)


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
):
    """Render the users management page."""
    current_admin = await get_current_admin_user_optional(request)
    if not current_admin:
        return RedirectResponse(url="/auth/login", status_code=302)

    env = get_templates(request)
    template = env.get_template("admin/users.html")

    html_content = template.render(
        request=request,
        user={
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role
        },
        active_page="users",
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

    env = get_templates(request)
    template = env.get_template("admin/csv_upload.html")

    html_content = template.render(
        request=request,
        user={
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role
        },
        active_page="csv-upload",
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
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role
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
            "name": current_admin.name,
            "email": current_admin.email,
            "role": current_admin.role
        },
        active_page="audit",
    )

    return HTMLResponse(content=html_content)