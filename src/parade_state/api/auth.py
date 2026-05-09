"""REST API authentication endpoints (JSON responses only).

This module contains REST API endpoints for authentication operations that
return JSON responses, not HTML redirects.

## Routes

### GET /api/v1/auth/me
Get current authenticated user information.

### POST /api/v1/auth/logout
Logout current user by invalidating session token.

## Architecture

These routes return JSON responses, not HTTP redirects. They are part of the
REST API, not the user-facing web interface.

**Key differences from web routes:**
- Returns JSON responses instead of redirects
- Requires Bearer token authentication
- Documented in OpenAPI/Swagger
- Intended for API clients (frontend JavaScript, mobile apps, etc.)

## Authentication Flow

### Login (via web routes)
1. Frontend: `window.location.href = '/auth/login'`
2. User completes OAuth flow
3. Frontend receives token: `?token=xxx`
4. Frontend stores token for API calls

### API Usage
```javascript
// Get current user
fetch('/api/v1/auth/me', {
    headers: {
        'Authorization': `Bearer ${token}`
    }
})
.then(res => res.json())
.then(data => {
    console.log('User:', data.email, data.name)
})

// Logout
fetch('/api/v1/auth/logout', {
    method: 'POST',
    headers: {
        'Authorization': `Bearer ${token}`
    }
})
.then(res => res.json())
.then(data => {
    console.log('Logged out:', data.message)
})
```

## Dependencies

This module depends on:
- `parade_state.auth.dependencies` - Authentication dependencies
- `parade_state.auth.session` - Session management
- `parade_state.models` - User model
- `parade_state.db` - Database sessions
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from parade_state.auth.dependencies import require_authenticated_user
from parade_state.auth.session import invalidate_session
from parade_state.db import get_db_session
from parade_state.models import User

router = APIRouter()
security = HTTPBearer()

# Alias for backward compatibility
get_current_user = require_authenticated_user


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get current user information.

    Returns profile information for the currently authenticated user.
    Requires valid Bearer token in Authorization header.

    Args:
        current_user: Authenticated user (injected by dependency)

    Returns:
        Dictionary containing user profile data:
        - id: User ID (string UUID)
        - email: User email address
        - name: User display name
        - role: User role (super_admin, admin, user)
        - status: Account status (active, pending, suspended, unrecognised)
        - access_level_id: Access level assignment (if any)

    Raises:
        HTTPException 401: If token invalid or user not found
        HTTPException 403: If user account is not active

    Example:
        ```bash
        curl -H "Authorization: Bearer abc123..." \\
             http://localhost:8000/api/v1/auth/me
        ```

    Response:
        ```json
        {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "email": "user@example.com",
            "name": "John Doe",
            "role": "admin",
            "status": "active",
            "access_level_id": "456e7890-e12b-34d5-b678-901234567890"
        }
        ```
    """
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
        "status": current_user.status,
        "access_level_id": str(current_user.access_level_id)
        if current_user.access_level_id
        else None,
    }


@router.post("/logout")
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Logout current user by invalidating session token.

    Invalidates the session token, requiring the user to re-authenticate
    for subsequent API calls.

    Args:
        credentials: HTTP Bearer credentials (auto-extracted by FastAPI)

    Returns:
        Dictionary confirming logout success

    Raises:
        HTTPException 401: If token invalid (session already expired)

    Example:
        ```bash
        curl -X POST \\
             -H "Authorization: Bearer abc123..." \\
             http://localhost:8000/api/v1/auth/logout
        ```

    Response:
        ```json
        {
            "message": "Logged out successfully"
        }
        ```

    Note:
        The client should also discard the stored token after successful logout.
        Invalidating the server-side session prevents token reuse, but client-side
        cleanup is recommended for best practices.
    """
    token = credentials.credentials

    async for db in get_db_session():
        success = await invalidate_session(db, token)
        if success:
            return {"message": "Logged out successfully"}

    # If we get here, session wasn't found
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired session token",
    )


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get current user information."""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
        "status": current_user.status,
        "access_level_id": str(current_user.access_level_id)
        if current_user.access_level_id
        else None,
    }


@router.post("/logout")
async def logout(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session),
):
    """Logout current user by invalidating session."""
    token = credentials.credentials
    await invalidate_session(db, token)

    return {"message": "Logged out successfully"}
