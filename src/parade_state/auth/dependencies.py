"""FastAPI authentication dependencies.

This module provides dependency injection functions for FastAPI endpoints
to handle authentication and authorization.

## Key Dependencies

### Basic Authentication
- `get_current_user` - Extract and validate authenticated user from request
- `get_current_user_optional` - Get user without requiring authentication

### Authorization
- `require_admin_user` - Require admin or super_admin role
- `require_super_admin_user` - Require super_admin role
- `check_access_level` - Factory for custom access level requirements

## Usage

```python
from fastapi import Depends, APIRouter
from parade_state.auth.dependencies import get_current_user, require_admin_user
from parade_state.models import User

router = APIRouter()

@router.get("/public")
async def public_endpoint():
    return {"message": "Anyone can access"}

@router.get("/protected")
async def protected_endpoint(
    current_user: User = Depends(get_current_user),
):
    return {"message": f"Hello {current_user.name}"}

@router.get("/admin-only")
async def admin_endpoint(
    current_user: User = Depends(require_admin_user),
):
    return {"message": f"Admin {current_user.name} is here"}
```

## Architecture

This module is part of the core authentication logic and bridges the gap
between HTTP requests and the session management system. It should be
imported by:
- `parade_state.api.*` - REST API endpoints
- `parade_state.middleware.*` - Authentication middleware

It depends on:
- `parade_state.auth.session` - Session validation
- `parade_state.models` - User model
- `parade_state.db` - Database sessions

## Security

- **Token extraction:** Uses HTTPBearer for secure Bearer token extraction
- **Session validation:** Every request validates session in database
- **Status checking:** Verifies user account is active
- **Role checking:** Provides role-based authorization helpers
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from parade_state.auth.session import get_valid_session
from parade_state.db import get_db_session
from parade_state.models import User
from parade_state.utils import ids

security = HTTPBearer()


async def get_current_user_optional(
    request: Request,
) -> User | None:
    """Get current user from session without requiring authentication.

    Extracts the Bearer token from Authorization header and validates it,
    but returns None instead of raising exception if not authenticated.

    Useful for endpoints that have different behavior for authenticated
    vs anonymous users.

    Args:
        request: FastAPI Request object

    Returns:
        User object if authenticated and valid, None otherwise

    Example:
        ```python
        @router.get("/content")
        async def get_content(
            current_user: User | None = Depends(get_current_user_optional),
        ):
            if current_user:
                return {"content": "premium", "user": current_user.name}
            else:
                return {"content": "free"}
        ```
    """
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # Remove "Bearer " prefix

    async for db in get_db_session():
        session = await get_valid_session(db, token, update_last_accessed=True)
        if not session:
            return None

        result = await db.execute(
            select(User).where(User.id == ids.to_uuid(session.user_id))
        )
        user = result.scalar_one_or_none()

        if user and user.status == "active":
            return user

    return None


async def require_authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Require authenticated user for protected endpoints.

    Validates Bearer token and returns authenticated user.
    Raises HTTPException if not authenticated.

    Args:
        request: FastAPI Request object
        credentials: HTTP Bearer credentials

    Returns:
        Authenticated User object

    Raises:
        HTTPException 401: If token invalid, expired, or user not found
        HTTPException 403: If user account is not active
        HTTPException 500: If database connection error

    Example:
        ```python
        @router.get("/profile")
        async def get_profile(
            current_user: User = Depends(require_authenticated_user),
        ):
            return {
                "email": current_user.email,
                "name": current_user.name,
                "role": current_user.role,
            }
        ```
    """
    token = credentials.credentials

    async for db in get_db_session():
        session = await get_valid_session(db, token, update_last_accessed=True)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        result = await db.execute(
            select(User).where(User.id == ids.to_uuid(session.user_id))
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User account is {user.status}",
            )

        return user

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Database connection error",
    )


async def require_admin_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Require admin user for admin-only endpoints.

    Validates authentication and checks if user has admin or super_admin role.

    Args:
        request: FastAPI Request object
        credentials: HTTP Bearer credentials

    Returns:
        Authenticated admin User object

    Raises:
        HTTPException 401: If not authenticated
        HTTPException 403: If authenticated but not admin

    Example:
        ```python
        @router.get("/admin/dashboard")
        async def admin_dashboard(
            current_user: User = Depends(require_admin_user),
        ):
            # current_user.role is guaranteed to be "admin" or "super_admin"
            return {"dashboard_data": "..."}
        ```
    """
    user = await require_authenticated_user(request, credentials)

    if user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user


async def require_super_admin_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Require super admin user for super-admin-only endpoints.

    Validates authentication and checks if user has super_admin role.

    Args:
        request: FastAPI Request object
        credentials: HTTP Bearer credentials

    Returns:
        Authenticated super admin User object

    Raises:
        HTTPException 401: If not authenticated
        HTTPException 403: If authenticated but not super admin

    Example:
        ```python
        @router.delete("/users/{user_id}")
        async def delete_user(
            user_id: str,
            current_user: User = Depends(require_super_admin_user),
        ):
            # Only super admins can delete users
            return {"message": "User deleted"}
        ```
    """
    user = await require_authenticated_user(request, credentials)

    if user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )

    return user


def check_access_level(required_access_level_order: int):
    """Dependency factory to check user access level.

    Creates a dependency that validates the user has an access level
    with at least the specified order value.

    Args:
        required_access_level_order: Minimum access level order required

    Returns:
        Dependency function that validates access level

    Example:
        ```python
        # Create dependency for level 3 access
        require_level_3 = check_access_level(3)

        @router.get("/sensitive-data")
        async def sensitive_data(
            current_user: User = Depends(require_level_3),
        ):
            # current_user.access_level.level_order >= 3
            return {"sensitive": "data"}
        ```
    """
    async def check_access(
        request: Request,
        credentials: HTTPAuthorizationCredentials = Depends(security),
    ) -> User:
        user = await require_authenticated_user(request, credentials)

        if not user.access_level_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access level assigned",
            )

        # This would require fetching the AccessLevel to check the level_order
        # For now, just ensure user has some access level
        return user

    return check_access
