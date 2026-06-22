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
4. Server creates session and redirects to frontend with token
5. Frontend stores token for API calls

### Example Frontend Code
```javascript
// Step 1: Redirect to login
window.location.href = '/auth/login';

// Step 2: Handle callback (frontend receives ?token=xxx)
const urlParams = new URLSearchParams(window.location.search);
const token = urlParams.get('token');
localStorage.setItem('auth_token', token);

// Step 3: Use token for API calls
fetch('/api/v1/users/me', {
    headers: {
        'Authorization': `Bearer ${token}`
    }
})
```

## Dependencies

This module depends on:
- `parade_state.auth.oauth` - OAuth client configuration
- `parade_state.auth.session` - Session creation and management
- `parade_state.models` - User model
- `parade_state.db` - Database sessions
- `parade_state.utils` - Environment and datetime utilities
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from parade_state.auth.oauth import get_oauth
from parade_state.auth.session import create_user_session
from parade_state.db import get_db_session
from parade_state.models import User
from parade_state.utils import cookies, env, utc_dt

router = APIRouter()


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
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/admin", status_code=302)

    # Check if a regular (non-admin) user is already authenticated
    current_user = await get_current_user_optional(request)
    if current_user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/deployment", status_code=302)

    # Show login page
    from jinja2 import Environment, FileSystemLoader
    from fastapi.responses import HTMLResponse
    from pathlib import Path

    templates_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), cache_size=0)
    template = env.get_template("login.html")

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


@router.get("/callback")
async def auth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Handle Google OAuth callback and create user session.

    Processes OAuth callback from Google, creates or updates user record,
    generates session token, and redirects to frontend with token.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        RedirectResponse to frontend with session token in URL

    Raises:
        HTTPException 500: If OAuth callback fails

    Example:
        ```python
        # After OAuth approval, Google redirects to:
        # /auth/callback?code=xxx&state=xxx

        # Server creates session and redirects to:
        # http://localhost:3000/auth/callback?token=abc123...
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
            # Auto-register user
            is_super_admin = super_admin_email == email

            user = User(
                email=email,
                name=name or email.split("@")[0],
                status="active" if is_super_admin else "pending",
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
        # Redirect admins to admin dashboard, regular users to deployment view
        redirect_path = "/admin" if user.role in ["admin", "super_admin"] else "/deployment"
        response = RedirectResponse(url=f"{base_url}{redirect_path}", status_code=302)

        # Set the authentication cookie using centralized cookie utility
        cookies.set_auth_cookie(response, user_session.token, expires_hours=24)

        return response

    except Exception as e:
        # Log error and return friendly message
        print(f"OAuth callback error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed",
        ) from None
