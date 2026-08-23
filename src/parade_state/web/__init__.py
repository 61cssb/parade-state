"""User-facing web routes.

This package contains user-facing web routes that return HTML responses or
HTTP redirects, as opposed to the REST API endpoints that return JSON.

## Routes

### `auth`
- OAuth login flows
- OAuth callback handling
- Authentication redirects

## Architecture

This package is intentionally separate from:
- `parade_state.api` - REST API endpoints (JSON responses)
- `parade_state.auth` - Authentication logic (reusable utilities)

## URL Structure

```
/auth/login      → Redirect to Google OAuth
/auth/callback   → OAuth callback, redirect to frontend
```

## Frontend Integration

Frontend should:
1. Direct users to `/auth/login` for authentication
2. The OAuth callback (`/auth/callback?code=xxx&state=xxx`) sets the
   HttpOnly session cookie server-side — no client-side token handling
3. Same-origin fetches authenticate via the cookie automatically;
   non-browser clients may send the same token as `Authorization: Bearer`

## Documentation

For authentication flow details, see:
- [ARCHITECTURE.md - Security Architecture](../../docs/ARCHITECTURE.md#7-security-architecture)
"""

from . import auth

__all__ = ["auth"]
