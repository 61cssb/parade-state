"""UUID Utilities for Parade State Application.

This module provides centralized UUID generation and validation to ensure consistency
across the application and provide better testability.

**Quick Start:**
    from parade_state.utils import uuid_gen

    # Generate a new UUID
    new_id = uuid_gen.uuid4()

    # Validate UUID string
    if uuid_gen.is_valid(user_id):
        process_user(user_id)

    # Convert to UUID object (with validation)
    user_uuid = uuid_gen.to_uuid(user_id_string)

**Why Use This Module:**
- **Consistent Generation**: Single pattern for UUID creation
- **Type Safety**: Built-in validation and conversion
- **Testability**: Easy to mock in tests
- **Error Handling**: Consistent error messages for invalid UUIDs
- **String Conversion**: Clean UUID to/from string conversion

**Key Functions:**

**Generation:**
- uuid_gen.uuid4() - Generate random UUID (version 4)
- uuid_gen.uuid4_str() - Generate UUID as string

**Validation:**
- uuid_gen.is_valid(value) - Check if value is valid UUID
- uuid_gen.validate(value) - Raise error if invalid UUID

**Conversion:**
- uuid_gen.to_uuid(value) - Convert string to UUID object
- uuid_gen.to_string(uuid_obj) - Convert UUID to string

**Database Compatibility:**
- uuid_gen.db_default() - Default function for database columns

**Common Patterns:**

*Generating new IDs:*
    user_id = uuid_gen.uuid4()
    deployment_id = uuid_gen.uuid4_str()

*Validating user input:*
    if uuid_gen.is_valid(user_id):
        user = get_user(user_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid user ID")

*Converting for database:*
    user_uuid = uuid_gen.to_uuid(user_id_string)
    result = await db.execute(select(User).where(User.id == user_uuid))

*In model definitions:*
    id: Mapped[str] = mapped_column(default=uuid_gen.db_default)

**Error Handling:**

All conversion functions raise ValueError with descriptive messages:

    >>> uuid_gen.to_uuid("invalid-uuid")
    ValueError: Invalid UUID format: 'invalid-uuid'
"""

import uuid as uuid_module
from typing import Union, Optional


def uuid4() -> uuid_module.UUID:
    """Generate a random UUID (version 4).

    Returns:
        New UUID object

    Example:
        >>> new_id = uuid_gen.uuid4()
        >>> print(type(new_id))
        <class 'uuid.UUID'>
    """
    return uuid_module.uuid4()


def uuid4_str() -> str:
    """Generate a random UUID as string.

    Returns:
        UUID as string (no braces, no hyphens in standard format)

    Example:
        >>> new_id = uuid_gen.uuid4_str()
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
        >>> uuid_gen.is_valid("12345678-1234-5678-1234-567812345678")
        True
        >>> uuid_gen.is_valid("invalid-uuid")
        False
        >>> uuid_gen.is_valid("not-a-uuid")
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
        >>> uuid_gen.validate("12345678-1234-5678-1234-567812345678")
        # No error raised
        >>> uuid_gen.validate("invalid-uuid")
        ValueError: Invalid UUID format: 'invalid-uuid'
    """
    if not isinstance(value, str):
        raise TypeError(f"UUID value must be string, got {type(value).__name__}")

    try:
        uuid_module.UUID(value)
    except ValueError as e:
        raise ValueError(f"Invalid UUID format: '{value}'") from e


def to_uuid(value: Union[str, uuid_module.UUID]) -> uuid_module.UUID:
    """Convert string to UUID object with validation.

    Args:
        value: UUID as string or UUID object

    Returns:
        UUID object

    Raises:
        ValueError: If value is not a valid UUID string
        TypeError: If value is not a string or UUID

    Example:
        >>> uuid_obj = uuid_gen.to_uuid("12345678-1234-5678-1234-567812345678")
        >>> print(type(uuid_obj))
        <class 'uuid.UUID'>
        >>> uuid_gen.to_uuid("invalid")
        ValueError: Invalid UUID format: 'invalid'
    """
    if isinstance(value, uuid_module.UUID):
        return value

    if not isinstance(value, str):
        raise TypeError(f"UUID value must be string or UUID, got {type(value).__name__}")

    try:
        return uuid_module.UUID(value)
    except ValueError as e:
        raise ValueError(f"Invalid UUID format: '{value}'") from e


def to_string(value: Union[str, uuid_module.UUID]) -> str:
    """Convert UUID to string format.

    Args:
        value: UUID object or string

    Returns:
        UUID as string

    Example:
        >>> uuid_str = uuid_gen.to_string(uuid_module.UUID('12345678-1234-5678-1234-567812345678'))
        >>> print(uuid_str)
        '12345678-1234-5678-1234-567812345678'
        >>> uuid_gen.to_string("already-string")
        'already-string'
    """
    if isinstance(value, str):
        # Validate it's a valid UUID string
        try:
            uuid_module.UUID(value)
        except ValueError:
            raise ValueError(f"Invalid UUID string: '{value}'")
        return value

    if isinstance(value, uuid_module.UUID):
        return str(value)

    raise TypeError(f"UUID value must be string or UUID, got {type(value).__name__}")


def db_default() -> str:
    """Default function for database UUID columns.

    Returns:
        UUID as string (for database storage)

    Example:
        >>> id: Mapped[str] = mapped_column(default=uuid_gen.db_default)
    """
    return str(uuid_module.uuid4())


def or_default(value: Optional[Union[str, uuid_module.UUID]], default: Optional[str] = None) -> Optional[str]:
    """Return UUID string or default value if None/invalid.

    Args:
        value: UUID object, string, or None
        default: Default value if value is None or invalid (default: None)

    Returns:
        UUID string or default

    Example:
        >>> uuid_gen.or_default("12345678-1234-5678-1234-567812345678")
        '12345678-1234-5678-1234-567812345678'
        >>> uuid_gen.or_default(None, "default-id")
        'default-id'
        >>> uuid_gen.or_default("invalid", None)
        None
    """
    if value is None:
        return default

    try:
        return to_string(value)
    except (ValueError, TypeError):
        return default
