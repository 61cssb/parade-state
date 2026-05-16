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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.dependencies import require_authenticated_user
from parade_state.auth.session import invalidate_session
from parade_state.db import get_db_session
from parade_state.models import User

router = APIRouter()
security = HTTPBearer()


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(require_authenticated_user),
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
