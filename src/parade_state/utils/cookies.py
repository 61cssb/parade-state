"""Cookie Management Utilities for Parade State Application.

This module provides centralized cookie management to ensure consistency
across the application and provide better security and maintainability.

**Quick Start:**
    from parade_state.utils import cookies

    # Set authentication cookie
    cookies.set_auth_cookie(response, session_token)

    # Clear authentication cookie
    cookies.clear_auth_cookie(response)

    # Get authentication token from request
    token = cookies.get_auth_token(request)

**Why Use This Module:**
- **Consistent Settings**: Single pattern for cookie parameters
- **Security**: Centralized security settings (httponly, samesite, secure)
- **Maintainability**: Change cookie behavior in one place
- **Type Safety**: Predictable cookie handling
- **Expiration Management**: Consistent expiration logic

**Key Functions:**

**Setting Cookies:**
- cookies.set_auth_cookie(response, token, expires_hours) - Set authentication cookie
- cookies.set_cookie(response, key, value, **kwargs) - Generic cookie setter

**Getting Cookies:**
- cookies.get_auth_token(request) - Get authentication token from request
- cookies.get_cookie(request, key) - Generic cookie getter

**Clearing Cookies:**
- cookies.clear_auth_cookie(response) - Clear authentication cookie
- cookies.clear_cookie(response, key) - Generic cookie clearer

**Common Patterns:**

*Setting authentication cookie:*
    response = RedirectResponse(url="/admin")
    cookies.set_auth_cookie(response, session.token, expires_hours=24)

*Getting authentication token:*
    token = cookies.get_auth_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

*Clearing authentication cookie:*
    response = RedirectResponse(url="/auth/login")
    cookies.clear_auth_cookie(response)

**Security Features:**

- **HttpOnly**: Prevents JavaScript access (XSS protection)
- **SameSite**: CSRF protection (lax for same-site redirects)
- **Secure**: HTTPS-only transmission (set True for production)
- **Path**: Cookie scope (set to "/" for entire site)
- **Domain**: Cookie domain scope (None for current domain)

**Cookie Parameters:**

All authentication cookies use these consistent parameters:
- httponly: True (prevents JavaScript access)
- samesite: "lax" (CSRF protection, allows same-site redirects)
- secure: False (set to True for HTTPS in production)
- path: "/" (entire site)
- domain: None (current domain only)
- max_age: 86400 seconds (24 hours)
"""

from typing import Any

from fastapi import Request
from starlette.responses import Response

from parade_state.utils import utc_dt

# Cookie configuration constants
AUTH_COOKIE_NAME = "session_token"
AUTH_COOKIE_MAX_AGE = 86400  # 24 hours in seconds
AUTH_COOKIE_PATH = "/"
AUTH_COOKIE_DOMAIN = None
AUTH_COOKIE_SAMESITE = "lax"
AUTH_COOKIE_SECURE = False  # Set to True for HTTPS in production
AUTH_COOKIE_HTTPONLY = True


def set_auth_cookie(
    response: Response,
    token: str,
    expires_hours: int = 24,
) -> None:
    """Set authentication cookie with consistent security parameters.

    Args:
        response: FastAPI/Starlette response object
        token: Session token to store in cookie
        expires_hours: Hours until cookie expires (default: 24)

    Example:
        ```python
        response = RedirectResponse(url="/admin")
        cookies.set_auth_cookie(response, session.token, expires_hours=24)
        ```
    """
    # Calculate expiration time
    expires = utc_dt.utcnow() + utc_dt.timedelta(hours=expires_hours)
    expires_str = expires.strftime("%a, %d-%b-%Y %H:%M:%S GMT")

    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        expires=expires_str,
        path=AUTH_COOKIE_PATH,
        domain=AUTH_COOKIE_DOMAIN,
        samesite=AUTH_COOKIE_SAMESITE,
        secure=AUTH_COOKIE_SECURE,
        httponly=AUTH_COOKIE_HTTPONLY,
    )


def get_auth_token(request: Request) -> str | None:
    """Get authentication token from request cookies.

    Args:
        request: FastAPI request object

    Returns:
        Session token string or None if not found

    Example:
        ```python
        token = cookies.get_auth_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        ```
    """
    return request.cookies.get(AUTH_COOKIE_NAME)


def clear_auth_cookie(response: Response) -> None:
    """Clear authentication cookie by setting it to expire in the past.

    Args:
        response: FastAPI/Starlette response object

    Example:
        ```python
        response = RedirectResponse(url="/auth/login")
        cookies.clear_auth_cookie(response)
        ```
    """
    # Set expiration to the past to clear cookie
    expires = utc_dt.utcnow() - utc_dt.timedelta(days=1)
    expires_str = expires.strftime("%a, %d-%b-%Y %H:%M:%S GMT")

    response.set_cookie(
        AUTH_COOKIE_NAME,
        "",  # Empty value
        expires=expires_str,
        path=AUTH_COOKIE_PATH,
        domain=AUTH_COOKIE_DOMAIN,
        samesite=AUTH_COOKIE_SAMESITE,
        secure=AUTH_COOKIE_SECURE,
        httponly=AUTH_COOKIE_HTTPONLY,
    )


def set_cookie(
    response: Response,
    key: str,
    value: str,
    expires_hours: int = 24,
    path: str = "/",
    domain: str | None = None,
    samesite: str = "lax",
    secure: bool = False,
    httponly: bool = True,
) -> None:
    """Generic cookie setter with custom parameters.

    Use this for non-auth cookies or custom cookie requirements.

    Args:
        response: FastAPI/Starlette response object
        key: Cookie name
        value: Cookie value
        expires_hours: Hours until cookie expires (default: 24)
        path: Cookie path scope (default: "/")
        domain: Cookie domain scope (default: None)
        samesite: SameSite attribute (default: "lax")
        secure: HTTPS-only flag (default: False)
        httponly: HttpOnly flag (default: True)

    Example:
        ```python
        response = RedirectResponse(url="/admin")
        cookies.set_cookie(response, "theme", "dark", expires_hours=8760)  # 1 year
        ```
    """
    expires = utc_dt.utcnow() + utc_dt.timedelta(hours=expires_hours)
    expires_str = expires.strftime("%a, %d-%b-%Y %H:%M:%S GMT")

    response.set_cookie(
        key,
        value,
        expires=expires_str,
        path=path,
        domain=domain,
        samesite=samesite,
        secure=secure,
        httponly=httponly,
    )


def get_cookie(request: Request, key: str) -> str | None:
    """Get cookie value from request.

    Args:
        request: FastAPI request object
        key: Cookie name

    Returns:
        Cookie value or None if not found

    Example:
        ```python
        theme = cookies.get_cookie(request, "theme")
        if theme:
            apply_theme(theme)
        ```
    """
    return request.cookies.get(key)


def clear_cookie(
    response: Response,
    key: str,
    path: str = "/",
    domain: str | None = None,
    samesite: str = "lax",
    secure: bool = False,
    httponly: bool = True,
) -> None:
    """Clear cookie by setting it to expire in the past.

    Args:
        response: FastAPI/Starlette response object
        key: Cookie name to clear
        path: Cookie path scope (default: "/")
        domain: Cookie domain scope (default: None)
        samesite: SameSite attribute (default: "lax")
        secure: HTTPS-only flag (default: False)
        httponly: HttpOnly flag (default: True)

    Example:
        ```python
        response = RedirectResponse(url="/home")
        cookies.clear_cookie(response, "theme")
        ```
    """
    expires = utc_dt.utcnow() - utc_dt.timedelta(days=1)
    expires_str = expires.strftime("%a, %d-%b-%Y %H:%M:%S GMT")

    response.set_cookie(
        key,
        "",  # Empty value
        expires=expires_str,
        path=path,
        domain=domain,
        samesite=samesite,
        secure=secure,
        httponly=httponly,
    )


def configure_production_settings() -> None:
    """Configure cookie settings for production environment.

    Call this during application startup for production environments.

    Effects:
    - Sets secure=True for HTTPS-only transmission
    - Updates other production-appropriate settings

    Example:
        ```python
        # In main.py during startup
        if env.get("ENVIRONMENT") == "production":
            cookies.configure_production_settings()
        ```
    """
    global AUTH_COOKIE_SECURE
    AUTH_COOKIE_SECURE = True  # Require HTTPS for production


def get_cookie_settings() -> dict[str, Any]:
    """Get current cookie configuration as dictionary.

    Returns:
        Dictionary with current cookie settings

    Example:
        ```python
        settings = cookies.get_cookie_settings()
        logger.info(f"Cookie settings: {settings}")
        ```
    """
    return {
        "auth_cookie_name": AUTH_COOKIE_NAME,
        "max_age": AUTH_COOKIE_MAX_AGE,
        "path": AUTH_COOKIE_PATH,
        "domain": AUTH_COOKIE_DOMAIN,
        "samesite": AUTH_COOKIE_SAMESITE,
        "secure": AUTH_COOKIE_SECURE,
        "httponly": AUTH_COOKIE_HTTPONLY,
    }
