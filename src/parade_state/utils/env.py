"""Environment Variable Utilities for Parade State Application.

This module provides centralized environment variable access to ensure consistency
across the application and provide better testability.

**Quick Start:**
    from parade_state.utils import env

    # Get environment variable with default
    database_url = env.get("DATABASE_URL", "sqlite:///:memory:")

    # Get required environment variable (raises error if not set)
    secret_key = env.get_required("SECRET_KEY")

    # Get as specific type
    debug_mode = env.get_bool("DEBUG", default=False)
    port = env.get_int("PORT", default=8000)

**Why Use This Module:**
- **Consistent Access**: Single pattern for environment variable access
- **Type Safety**: Built-in type conversion and validation
- **Testability**: Easy to mock in tests
- **Error Handling**: Consistent error messages for missing variables
- **Defaults**: Clean default value handling

**Key Functions:**

**Basic Access:**
- env.get(key, default=None) - Get env var with optional default
- env.get_required(key) - Get env var, raise error if missing

**Type-Specific Access:**
- env.get_bool(key, default=False) - Get as boolean
- env.get_int(key, default=0) - Get as integer
- env.get_float(key, default=0.0) - Get as float
- env.get_list(key, separator=",", default=[]) - Get as list

**Validation:**
- env.get_url(key, default=None) - Get and validate URL
- env.get_email(key, default=None) - Get and validate email

**Common Patterns:**

*Getting configuration:*
    database_url = env.get("DATABASE_URL", "sqlite:///:memory:")
    secret_key = env.get_required("SECRET_KEY")

*Type conversion:*
    debug = env.get_bool("DEBUG", default=False)
    port = env.get_int("PORT", default=8000)
    allowed_origins = env.get_list("ALLOWED_ORIGINS", separator=",")

*With validation:*
    frontend_url = env.get_url("FRONTEND_URL", "http://localhost:3000")
    admin_email = env.get_email("SUPER_ADMIN_EMAIL")
"""

import os
from urllib.parse import urlparse


def get(key: str, default: str | None = None) -> str | None:
    """Get environment variable value with optional default.

    Args:
        key: Environment variable name
        default: Default value if not found (default: None)

    Returns:
        Environment variable value or default

    Example:
        >>> env.get("DATABASE_URL")
        'postgresql://localhost/mydb'
        >>> env.get("MISSING_VAR", "default_value")
        'default_value'
    """
    return os.getenv(key, default)


def get_required(key: str) -> str:
    """Get required environment variable, raise error if not set.

    Args:
        key: Environment variable name

    Returns:
        Environment variable value

    Raises:
        ValueError: If environment variable is not set

    Example:
        >>> env.get_required("SECRET_KEY")
        'my-secret-key'
        >>> env.get_required("MISSING_VAR")
        ValueError: Required environment variable 'MISSING_VAR' is not set
    """
    value = os.getenv(key)
    if value is None:
        raise ValueError(f"Required environment variable '{key}' is not set")
    return value


def get_bool(key: str, default: bool = False) -> bool:
    """Get environment variable as boolean.

    Accepts: 'true', '1', 'yes', 'on' (case-insensitive) as True
             'false', '0', 'no', 'off' (case-insensitive) as False

    Args:
        key: Environment variable name
        default: Default value if not found (default: False)

    Returns:
        Boolean value

    Example:
        >>> env.get_bool("DEBUG", default=False)
        True
        >>> env.get_bool("FEATURE_FLAG", default=True)
        False
    """
    value = os.getenv(key)
    if value is None:
        return default

    # Convert to lowercase and check for truthy values
    value_lower = value.lower()
    if value_lower in ("true", "1", "yes", "on"):
        return True
    elif value_lower in ("false", "0", "no", "off"):
        return False
    else:
        # If value is not recognized, return default
        return default


def get_int(key: str, default: int = 0) -> int:
    """Get environment variable as integer.

    Args:
        key: Environment variable name
        default: Default value if not found or invalid (default: 0)

    Returns:
        Integer value

    Example:
        >>> env.get_int("PORT", default=8000)
        3000
        >>> env.get_int("TIMEOUT", default=30)
        60
    """
    value = os.getenv(key)
    if value is None:
        return default

    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    """Get environment variable as float.

    Args:
        key: Environment variable name
        default: Default value if not found or invalid (default: 0.0)

    Returns:
        Float value

    Example:
        >>> env.get_float("TAX_RATE", default=0.0)
        0.08
        >>> env.get_float("DISCOUNT", default=0.0)
        0.15
    """
    value = os.getenv(key)
    if value is None:
        return default

    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def get_list(
    key: str, separator: str = ",", default: list[str] | None = None
) -> list[str]:
    """Get environment variable as list of strings.

    Args:
        key: Environment variable name
        separator: String to split on (default: ",")
        default: Default value if not found (default: empty list)

    Returns:
        List of string values

    Example:
        >>> env.get_list("ALLOWED_ORIGINS", separator=",")
        ['http://localhost:3000', 'https://example.com']
        >>> env.get_list("TAGS", separator=";")
        ['python', 'fastapi', 'uvicorn']
    """
    if default is None:
        default = []

    value = os.getenv(key)
    if value is None:
        return default

    # Split and strip whitespace from each item
    items = [item.strip() for item in value.split(separator)]
    # Filter out empty strings
    return [item for item in items if item]


def get_url(key: str, default: str | None = None) -> str | None:
    """Get environment variable as URL with basic validation.

    Args:
        key: Environment variable name
        default: Default value if not found (default: None)

    Returns:
        URL string or default

    Raises:
        ValueError: If value is not a valid URL

    Example:
        >>> env.get_url("FRONTEND_URL", "http://localhost:3000")
        'http://localhost:3000'
        >>> env.get_url("API_URL")
        'https://api.example.com'
    """
    value = os.getenv(key, default)
    if value is None:
        return None

    # Basic URL validation
    try:
        result = urlparse(value)
        if not all([result.scheme, result.netloc]):
            raise ValueError(f"Invalid URL: {value}")
        return value
    except Exception as e:
        raise ValueError(f"Invalid URL for environment variable '{key}': {e}") from e


def get_email(key: str, default: str | None = None) -> str | None:
    """Get environment variable as email address with basic validation.

    Args:
        key: Environment variable name
        default: Default value if not found (default: None)

    Returns:
        Email address or default

    Raises:
        ValueError: If value is not a valid email format

    Example:
        >>> env.get_email("ADMIN_EMAIL")
        'admin@example.com'
        >>> env.get_email("USER_EMAIL", default=None)
        'user@example.com'
    """
    value = os.getenv(key, default)
    if value is None:
        return None

    # Basic email validation
    if "@" not in value or "." not in value.split("@")[-1]:
        raise ValueError(
            f"Invalid email format for environment variable '{key}': {value}"
        )

    return value


def is_set(key: str) -> bool:
    """Check if environment variable is set (not None or empty).

    Args:
        key: Environment variable name

    Returns:
        True if environment variable is set and not empty

    Example:
        >>> env.is_set("SECRET_KEY")
        True
        >>> env.is_set("OPTIONAL_FEATURE")
        False
    """
    value = os.getenv(key)
    return value is not None and value != ""


def environ() -> dict[str, str]:
    """Snapshot of the process environment for spawning subprocesses.

    Returns:
        Copy of the current environment as a plain dict, safe to mutate
        before handing to ``asyncio.create_subprocess_exec(env=...)``.

    Example:
        >>> child_env = env.environ()
        >>> child_env["PGPASSWORD"] = "secret"
    """
    return dict(os.environ)
