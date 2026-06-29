"""ID Generation Utilities for Parade State Application.

This module provides centralized identifier generation and validation to ensure consistency
across the application and provide better testability. Currently focused on UUID generation,
this module may expand to support other ID types in the future.

**Quick Start:**
    from parade_state.utils import ids

    # Generate a new UUID
    new_id = ids.uuid4()

    # Validate UUID string
    if ids.is_valid(user_id):
        process_user(user_id)

    # Convert to UUID object (with validation)
    user_uuid = ids.to_uuid(user_id_string)

**Why Use This Module:**
- **Consistent Generation**: Single pattern for ID creation
- **Type Safety**: Built-in validation and conversion
- **Testability**: Easy to mock in tests
- **Error Handling**: Consistent error messages for invalid IDs
- **String Conversion**: Clean UUID to/from string conversion

**Key Functions:**

**Generation:**
- ids.uuid4() - Generate random UUID (version 4)
- ids.uuid4_str() - Generate UUID as string
- ids.short_id() - Generate a random 8-char base62 short ID (personnel identifier)
- ids.mint_unique_short_id(is_taken) - Mint a short ID guaranteed unique per a taken-check

**Validation:**
- ids.is_valid(value) - Check if value is valid UUID
- ids.validate(value) - Raise error if invalid UUID

**Conversion:**
- ids.to_uuid(value) - Convert string to UUID object
- ids.to_string(uuid_obj) - Convert UUID to string

**Database Compatibility:**
- ids.db_default() - Default function for database columns

**Common Patterns:**

*Generating new IDs:*
    user_id = ids.uuid4()
    deployment_id = ids.uuid4_str()

*Validating user input:*
    if ids.is_valid(user_id):
        user = get_user(user_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid user ID")

*Converting for database:*
    user_uuid = ids.to_uuid(user_id_string)
    result = await db.execute(select(User).where(User.id == user_uuid))

*In model definitions:*
    id: Mapped[str] = mapped_column(default=ids.db_default)

**Error Handling:**

All conversion functions raise ValueError with descriptive messages:

    >>> ids.to_uuid("invalid-uuid")
    ValueError: Invalid UUID format: 'invalid-uuid'
"""

import uuid as uuid_module
from collections.abc import Callable
from typing import Protocol

# Base62 alphabet (URL-safe, no ambiguous chars). Used for shortened personnel IDs.
# Ordered digits-first so lexicographic sort is stable; omits 0/O/1/l/I look-alikes.
_BASE62_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_SHORT_ID_LENGTH = 8


def short_id(length: int = _SHORT_ID_LENGTH) -> str:
    """Generate a random shortened ID using the base62 alphabet.

    Uses ``secrets`` for a CSPRNG-backed draw. With the default 8-char length the
    keyspace is 62**8 (~2.18e14); collision resistance for up to 100K lifetime IDs
    is provided by pairing this with :func:`mint_unique_short_id`, which retries
    on collision against a taken-check.

    Args:
        length: Number of characters to generate (default 8).

    Returns:
        A random short ID string, e.g. ``"7Kq2F9xZ"``.
    """
    import secrets

    return "".join(secrets.choice(_BASE62_ALPHABET) for _ in range(length))


class _ShortIdTakenChecker(Protocol):
    def __call__(self, candidate: str) -> bool: ...


def mint_unique_short_id(
    is_taken: Callable[[str], bool],
    *,
    max_attempts: int = 32,
    length: int = _SHORT_ID_LENGTH,
) -> str:
    """Mint a short ID guaranteed not to collide per ``is_taken``.

    Repeatedly draws a fresh short ID until ``is_taken(candidate)`` returns False
    (or ``max_attempts`` is exhausted). The caller supplies ``is_taken`` so this
    stays framework-agnostic — typically a DB lookup against existing short IDs.

    Args:
        is_taken: Callable returning True if ``candidate`` is already in use.
        max_attempts: Give-up threshold (raises RuntimeError). 32 attempts against
            a ~2.18e14 keyspace makes collision exhaustion effectively impossible.
        length: Short ID length (default 8).

    Returns:
        A short ID for which ``is_taken`` returned False.

    Raises:
        RuntimeError: If every attempt collided (extremely unlikely).
    """
    for _ in range(max_attempts):
        candidate = short_id(length)
        if not is_taken(candidate):
            return candidate
    raise RuntimeError(
        f"Could not mint a unique {length}-char short_id after {max_attempts} attempts"
    )


def uuid4() -> uuid_module.UUID:
    """Generate a random UUID (version 4).

    Returns:
        New UUID object

    Example:
        >>> new_id = ids.uuid4()
        >>> print(type(new_id))
        <class 'uuid.UUID'>
    """
    return uuid_module.uuid4()


def uuid4_str() -> str:
    """Generate a random UUID as string.

    Returns:
        UUID as string (no braces, no hyphens in standard format)

    Example:
        >>> new_id = ids.uuid4_str()
        >>> print(type(new_id))
        <class 'str'>
        >>> print(len(new_id))
        36  # Standard UUID format: 12345678-1234-5678-1234-567812345678
    """
    return str(uuid_module.uuid4())


def is_valid(value: str) -> bool:
    """Check if value is a valid UUID string.

    Args:
        value: String to validate

    Returns:
        True if valid UUID format, False otherwise

    Example:
        >>> ids.is_valid("12345678-1234-5678-1234-567812345678")
        True
        >>> ids.is_valid("invalid-uuid")
        False
        >>> ids.is_valid("not-a-uuid")
        False
    """
    if not isinstance(value, str):
        return False

    try:
        uuid_module.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def validate(value: str) -> None:
    """Validate UUID string, raise error if invalid.

    Args:
        value: String to validate

    Raises:
        ValueError: If value is not a valid UUID
        TypeError: If value is not a string

    Example:
        >>> ids.validate("12345678-1234-5678-1234-567812345678")
        # No error raised
        >>> ids.validate("invalid-uuid")
        ValueError: Invalid UUID format: 'invalid-uuid'
    """
    if not isinstance(value, str):
        raise TypeError(f"UUID value must be string, got {type(value).__name__}")

    try:
        uuid_module.UUID(value)
    except ValueError as e:
        raise ValueError(f"Invalid UUID format: '{value}'") from e


def to_uuid(value: str | uuid_module.UUID) -> uuid_module.UUID:
    """Convert string to UUID object with validation.

    Args:
        value: UUID as string or UUID object

    Returns:
        UUID object

    Raises:
        ValueError: If value is not a valid UUID string
        TypeError: If value is not a string or UUID

    Example:
        >>> uuid_obj = ids.to_uuid("12345678-1234-5678-1234-567812345678")
        >>> print(type(uuid_obj))
        <class 'uuid.UUID'>
        >>> ids.to_uuid("invalid")
        ValueError: Invalid UUID format: 'invalid'
    """
    if isinstance(value, uuid_module.UUID):
        return value

    if not isinstance(value, str):
        raise TypeError(
            f"UUID value must be string or UUID, got {type(value).__name__}"
        )

    try:
        return uuid_module.UUID(value)
    except ValueError as e:
        raise ValueError(f"Invalid UUID format: '{value}'") from e


def to_string(value: str | uuid_module.UUID) -> str:
    """Convert UUID to string format.

    Args:
        value: UUID object or string

    Returns:
        UUID as string

    Example:
        >>> uuid_str = ids.to_string(uuid_module.UUID('12345678-1234-5678-1234-567812345678'))
        >>> print(uuid_str)
        '12345678-1234-5678-1234-567812345678'
        >>> ids.to_string("already-string")
        'already-string'
    """
    if isinstance(value, str):
        # Validate it's a valid UUID string
        try:
            uuid_module.UUID(value)
        except ValueError as err:
            raise ValueError(f"Invalid UUID string: '{value}'") from err
        return value

    if isinstance(value, uuid_module.UUID):
        return str(value)

    raise TypeError(f"UUID value must be string or UUID, got {type(value).__name__}")


def db_default() -> str:
    """Default function for database UUID columns.

    Returns:
        UUID as string (for database storage)

    Example:
        >>> id: Mapped[str] = mapped_column(default=ids.db_default)
    """
    return str(uuid_module.uuid4())


def or_default(
    value: str | uuid_module.UUID | None, default: str | None = None
) -> str | None:
    """Return UUID string or default value if None/invalid.

    Args:
        value: UUID object, string, or None
        default: Default value if value is None or invalid (default: None)

    Returns:
        UUID string or default

    Example:
        >>> ids.or_default("12345678-1234-5678-1234-567812345678")
        '12345678-1234-5678-1234-567812345678'
        >>> ids.or_default(None, "default-id")
        'default-id'
        >>> ids.or_default("invalid", None)
        None
    """
    if value is None:
        return default

    try:
        return to_string(value)
    except (ValueError, TypeError):
        return default
