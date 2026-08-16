"""User-facing authentication routes (OAuth flows).

This module contains user-facing web routes for OAuth authentication,
including login initiation and callback handling.

## Routes

### GET /auth/login
Initiates Google OAuth login flow by redirecting user to Google consent screen.

### GET /auth/callback
Handles OAuth callback from Google, creates user session, and redirects
to frontend with session token.

## Architecture

These routes return HTTP redirects, not JSON responses. They are part of
the user-facing web interface, not the REST API.

**Key differences from REST API:**
- Returns RedirectResponse instead of JSON
- Handles browser-based OAuth flows
- No OpenAPI documentation
- Intended for frontend navigation, not API clients

## Frontend Integration

### Login Flow
1. Frontend redirects browser to `/auth/login`
2. User authorizes with Google
3. Google redirects to `/auth/callback?code=xxx&state=xxx`
4. Server creates a session (admin-only) and sets the session cookie
5. Browser is redirected to `/admin`; the cookie authenticates API calls

Non-admin sign-ins get the no-access page and no session cookie.

### Example Frontend Code
```javascript
// Step 1: Redirect to login
window.location.href = '/auth/login';

// Step 2: The callback sets the session cookie server-side and
// redirects to /admin — no client-side token handling needed.

// Step 3: Use the cookie for API calls (sent automatically)
fetch('/api/v1/users/me')
```

## Dependencies

This module depends on:
- `parade_state.auth.oauth` - OAuth client configuration
- `parade_state.auth.session` - Session creation and management
- `parade_state.models` - User model
- `parade_state.db` - Database sessions
- `parade_state.utils` - Environment and datetime utilities
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from parade_state.auth.oauth import get_oauth
from parade_state.auth.session import create_user_session
from parade_state.db import get_db_session
from parade_state.models import User
from parade_state.utils import cookies, env, utc_dt

router = APIRouter()

logger = logging.getLogger(__name__)

ADMIN_ROLES = ("admin", "super_admin")


def _render_no_access(user_email: str | None = None) -> HTMLResponse:
    """Render the 'no access' page shown to non-admin sign-ins.

    The system is admin-only: sign-ins that are not active admins get this
    page and no session. Status 403 tells browsers (and tests) the account
    is authenticated-but-forbidden rather than merely lost.
    """
    templates_dir = Path(__file__).parent.parent / "templates"
    template_env = Environment(
        loader=FileSystemLoader(str(templates_dir)), cache_size=0
    )
    template = template_env.get_template("no_access.html")
    return HTMLResponse(
        content=template.render(user_email=user_email),
        status_code=status.HTTP_403_FORBIDDEN,
    )


@router.get("/login")
async def login(request: Request):
    """Initiate Google OAuth login flow.

    Shows login page with button to initiate OAuth flow.

    Args:
        request: FastAPI Request object

    Returns:
        HTML login page with OAuth button

    Example:
        ```python
        # Frontend redirects user to login
        window.location.href = '/auth/login'
        ```
    """
    # Check if user is already authenticated
    from parade_state.auth.admin_dependencies import (
        get_current_admin_user_optional,
        get_current_user_optional,
    )

    current_admin = await get_current_admin_user_optional(request)
    if current_admin:
        return RedirectResponse(url="/admin", status_code=302)

    # Authenticated but not an admin: admin-only system, so no surface to
    # send them to.
    current_user = await get_current_user_optional(request)
    if current_user:
        return RedirectResponse(url="/auth/no-access", status_code=302)

    # Show login page
    templates_dir = Path(__file__).parent.parent / "templates"
    template_env = Environment(
        loader=FileSystemLoader(str(templates_dir)), cache_size=0
    )
    template = template_env.get_template("login.html")

    html_content = template.render(request=request)
    return HTMLResponse(content=html_content)


@router.get("/oauth/start")
async def start_oauth(request: Request):
    """Start the OAuth flow with Google.

    Redirects user to Google OAuth consent screen for authorization.

    Args:
        request: FastAPI Request object

    Returns:
        RedirectResponse to Google OAuth
    """
    # Build redirect URI dynamically from the incoming request
    base_url = f"{request.url.scheme}://{request.url.netloc}"
    redirect_uri = f"{base_url}/auth/callback"

    oauth = get_oauth()
    google = oauth.create_client("google")
    return await google.authorize_redirect(request, redirect_uri)


@router.get("/logout")
async def logout(request: Request):
    """Logout user by clearing session cookie and redirecting to login."""
    # Create redirect response to login page
    response = RedirectResponse(url="/auth/login", status_code=302)

    # Clear the session cookie using centralized cookie utility
    cookies.clear_auth_cookie(response)

    return response


@router.get("/no-access")
async def no_access():
    """Render the 'no access' page for non-admin users.

    Reached via redirects from viewer-facing routes (gated on admin role)
    and from the login page for authenticated non-admins. The OAuth
    callback renders this page directly (with the sign-in email) instead
    of redirecting.
    """
    return _render_no_access()


@router.get("/callback")
async def auth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Handle Google OAuth callback and create user session.

    Processes OAuth callback from Google, creates or updates user record,
    and — for active admins — generates a session token and redirects to
    the admin dashboard. The system is admin-only: unknown sign-ins are
    auto-registered as `unrecognised` and shown the no-access page without
    receiving a session.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        RedirectResponse to /admin with the session cookie set, or the
        no-access page (403) for sign-ins that are not active admins

    Raises:
        HTTPException 403: If the account is suspended
        HTTPException 500: If OAuth callback fails

    Example:
        ```python
        # After OAuth approval, Google redirects to:
        # /auth/callback?code=xxx&state=xxx

        # Server creates session and redirects to:
        # http://localhost:8000/admin
        ```
    """
    try:
        oauth = get_oauth()
        google = oauth.create_client("google")
        token = await google.authorize_access_token(request)
        user_info = token.get("userinfo")

        if not user_info:
            user_info = await google.parse_id_token(request, token)

        email = user_info.get("email")
        name = user_info.get("name")

        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not provided by Google OAuth",
            )

        # Check if user exists
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        # Check for super admin bootstrap
        super_admin_email = env.get("SUPER_ADMIN_EMAIL")

        if not user:
            # Auto-register user. Unknown sign-ins are unrecognised until a
            # super-admin promotes them via /admin/users; only the bootstrap
            # super-admin account is created ready-to-use.
            is_super_admin = super_admin_email == email

            user = User(
                email=email,
                name=name or email.split("@")[0],
                status="active" if is_super_admin else "unrecognised",
                role="super_admin" if is_super_admin else "user",
                first_sign_in_at=utc_dt.utcnow(),
                last_sign_in_at=utc_dt.utcnow(),
            )

            db.add(user)
            await db.commit()
            await db.refresh(user)

        else:
            # Update last sign in
            user.last_sign_in_at = utc_dt.utcnow()

            # Update user info if changed
            if name:
                user.name = name

            await db.commit()

        # Check user status
        if user.status == "suspended":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account suspended",
            )

        # Admin-only system: anyone who is not an active admin gets the
        # no-access page and no session (so they cannot reach any
        # authenticated page until promoted).
        if user.status != "active" or user.role not in ADMIN_ROLES:
            return _render_no_access(user_email=user.email)

        # Create session
        user_session = await create_user_session(
            db,
            user_id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )

        # Build redirect URL dynamically and set cookie server-side
        base_url = f"{request.url.scheme}://{request.url.netloc}"

        # Create redirect response with authentication cookie
        response = RedirectResponse(url=f"{base_url}/admin", status_code=302)

        # Set the authentication cookie using centralized cookie utility
        cookies.set_auth_cookie(response, user_session.token, expires_hours=24)

        return response

    except HTTPException:
        # Deliberate error responses (e.g. suspended accounts) must keep
        # their status code instead of being masked as a 500 below.
        raise
    except Exception:
        # Log error (with traceback) and return friendly message
        logger.exception("OAuth callback failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed",
        ) from None
