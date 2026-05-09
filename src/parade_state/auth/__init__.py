"""Authentication and authorization utilities.

This package provides the core authentication logic for the Parade State application,
separated from both the REST API and user-facing web routes.

## Modules

### `session`
- Session management utilities
- UserSession database operations
- Token generation and validation
- Session lifecycle management

### `dependencies`
- FastAPI dependency injection for authentication
- User context injection
- Authorization helpers (require_admin, require_super_admin)
- Session validation

### `oauth`
- OAuth client configuration
- Google OAuth setup
- Integration with authlib

## Architecture

This package is intentionally separate from:
- `parade_state.api` - REST API endpoints (JSON responses)
- `parade_state.web` - User-facing web routes (HTML/redirects)

This separation ensures:
1. Reusable authentication logic across API and web
2. Clear separation of concerns
3. Easier testing of auth logic
4. Flexible frontend implementation

## Usage

```python
from parade_state.auth.dependencies import get_current_user, require_admin
from parade_state.auth.session import create_user_session, get_valid_session
from parade_state.auth.oauth import get_oauth

# In API endpoints
@router.get("/api/v1/users")
async def list_users(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    # current_user is guaranteed to be admin
    pass

# Session management
session = await create_user_session(db, user_id, email, name, role)
is_valid = await get_valid_session(db, token)
```

For detailed authentication flow, see:
- [ARCHITECTURE.md - Authentication Flow](../../docs/ARCHITECTURE.md#7-security-architecture)
"""

__all__ = ["dependencies", "oauth", "session"]
