"""UTC Datetime Utilities for Parade State Application.

This module provides centralized UTC datetime handling to ensure consistency
across the application and eliminate timezone confusion.

**Quick Start:**
    from parade_state.utils import utc_dt

    # Get current UTC time (always timezone-aware)
    now = utc_dt.utcnow()

    # Add 7 days while preserving timezone awareness
    expires = utc_dt.add_timedelta(now, days=7)

    # Store in database (SQLite compatible)
    db_time = utc_dt.ensure_naive(expires)

    # Check expiration
    if utc_dt.is_expired(session.expires_at):
        raise HTTPException(status_code=401, detail="Session expired")

**Why Use This Module:**
- **Consistent Timezone Handling**: All operations use UTC, eliminating timezone confusion
- **Database Compatibility**: Proper naive/aware datetime handling for SQLite/PostgreSQL
- **Maintainability**: Change datetime behavior in one place
- **Type Safety**: Predictable return types and behavior
- **No Deprecated Functions**: Uses timezone-aware datetime.now() instead of deprecated utcnow()

**Common Patterns:**

*Getting current time:*
    now = utc_dt.utcnow()  # Always timezone-aware UTC

*Time arithmetic:*
    future = utc_dt.add_timedelta(now, days=7, hours=2)

*Database storage:*
    # Store as naive for SQLite compatibility
    session.expires_at = utc_dt.ensure_naive(utc_dt.utcnow())

*Business logic:*
    # Use as timezone-aware for comparisons
    if utc_dt.ensure_aware(session.expires_at) > utc_dt.utcnow():
        # Session is still valid
        pass

*Expiration checking:*
    if utc_dt.is_expired(deployment.valid_until):
        # Deployment is expired
        pass

**Key Functions:**

**Time Retrieval:**
- utc_dt.utcnow() - Current UTC time (timezone-aware)
- utc_dt.utc_from_timestamp(timestamp) - Convert timestamp to UTC datetime

**Conversion:**
- utc_dt.ensure_aware(dt_naive) - Make naive datetime timezone-aware (assumes UTC)
- utc_dt.ensure_naive(dt_aware) - Convert to naive for database storage
- utc_dt.to_utc(dt_input) - Convert any datetime to UTC

**Time Calculations:**
- utc_dt.add_timedelta(base_time, **kwargs) - Add timedelta preserving timezone
- utc_dt.is_expired(expiry_time) - Check if time has passed
- utc_dt.is_valid_time_window(start, end) - Check if current time is in range

**Formatting:**
- utc_dt.format_datetime(dt_input, format_string) - Format datetime as string
- utc_dt.parse_datetime(datetime_string, format_string) - Parse to timezone-aware datetime

**Constants:**
- utc_dt.ONE_DAY, utc_dt.ONE_WEEK, utc_dt.ONE_MONTH, utc_dt.ONE_YEAR

**Helpers:**
- utc_dt.get_age(birth_date) - Calculate age from birth date
- utc_dt.truncate_to_day(dt_input) - Truncate to start of day
- utc_dt.get_default_session_expiry() - 7-day session expiry
- utc_dt.get_default_cache_expiry() - 1-day cache expiry

**Database Compatibility:**

SQLite doesn't handle timezone-aware datetimes well, so we use this pattern:
1. Store datetimes as naive (no timezone info) in database
2. Convert to timezone-aware for business logic
3. Use ensure_naive() before database operations
4. Use ensure_aware() after database retrieval

**Example:**
    # Create session with 7-day expiry
    session = UserSession(
        expires_at=utc_dt.ensure_naive(utc_dt.add_timedelta(utc_dt.utcnow(), days=7))
    )

    # Check if session is still valid
    if utc_dt.ensure_aware(session.expires_at) > utc_dt.utcnow():
        # Session valid
        pass
"""

import datetime as dt
from datetime import date, datetime, timedelta

# Re-export commonly used types for type annotations
__all__ = [
    "date",
    "datetime",
    "timedelta",
    "UTC",
]

# Constants for datetime handling
UTC = dt.UTC


def utcnow() -> datetime:
    """Get current UTC datetime as timezone-aware datetime.

    This replaces datetime.utcnow() which is deprecated and returns
    timezone-aware datetime for consistent handling.

    Returns:
        Current UTC datetime with timezone info
    """
    return datetime.now(UTC)


def utc_from_timestamp(timestamp: float) -> datetime:
    """Convert timestamp to UTC datetime.

    Args:
        timestamp: Unix timestamp

    Returns:
        Timezone-aware UTC datetime
    """
    return datetime.fromtimestamp(timestamp, UTC)


def ensure_aware(dt_naive: datetime | dt.datetime) -> datetime:
    """Ensure a datetime is timezone-aware by adding UTC if naive.

    Args:
        dt_naive: Naive or aware datetime

    Returns:
        Timezone-aware datetime (UTC if was naive, original timezone if aware)
    """
    if dt_naive.tzinfo is None:
        return dt_naive.replace(tzinfo=UTC)
    return dt_naive


def ensure_naive(dt_aware: datetime | dt.datetime) -> datetime:
    """Convert timezone-aware datetime to naive datetime (remove timezone info).

    Useful for database operations that expect naive datetimes.

    Args:
        dt_aware: Timezone-aware datetime

    Returns:
        Naive datetime (timezone info removed)
    """
    if dt_aware.tzinfo is not None:
        return dt_aware.replace(tzinfo=None)
    return dt_aware


def to_utc(dt_input: datetime | dt.datetime) -> datetime:
    """Convert any datetime to UTC timezone.

    Args:
        dt_input: Datetime (aware or naive)

    Returns:
        Timezone-aware datetime in UTC
    """
    if dt_input.tzinfo is None:
        # Assume naive datetimes are already in UTC
        return dt_input.replace(tzinfo=UTC)
    # Convert to UTC
    return dt_input.astimezone(UTC)


def is_expired(expiry_time: datetime | dt.datetime) -> bool:
    """Check if an expiry time has passed.

    Args:
        expiry_time: Expiry datetime to check (aware or naive)

    Returns:
        True if expired, False otherwise
    """
    now = utcnow()

    # Handle both aware and naive datetimes
    if expiry_time.tzinfo is None:
        now = ensure_naive(now)
    else:
        now = ensure_aware(now)

    return now > expiry_time


def is_valid_time_window(
    start_time: datetime | dt.datetime, end_time: datetime | dt.datetime
) -> bool:
    """Check if current time is within a time window.

    Args:
        start_time: Window start time (aware or naive)
        end_time: Window end time (aware or naive)

    Returns:
        True if current time is within window, False otherwise
    """
    now = utcnow()

    # Normalize comparison datetimes
    if start_time.tzinfo is None:
        now_for_start = ensure_naive(now)
    else:
        now_for_start = ensure_aware(now)

    return start_time <= now_for_start <= end_time


def add_timedelta(base_time: datetime | dt.datetime, **timedelta_kwargs) -> datetime:
    """Add timedelta to a datetime while preserving timezone awareness.

    Args:
        base_time: Base datetime
        **timedelta_kwargs: Arguments to pass to timedelta (days, hours, etc.)

    Returns:
        New datetime with added timedelta, preserving timezone awareness
    """
    delta = timedelta(**timedelta_kwargs)
    result = base_time + delta

    # Preserve timezone awareness
    if base_time.tzinfo is not None:
        return ensure_aware(result)
    return result


def format_datetime(
    dt_input: datetime | dt.datetime, format_string: str = "%Y-%m-%d %H:%M:%S %Z"
) -> str:
    """Format datetime as string.

    Args:
        dt_input: Datetime to format
        format_string: Format string (default: ISO-like with timezone)

    Returns:
        Formatted datetime string
    """
    if dt_input.tzinfo is None:
        dt_input = ensure_aware(dt_input)
    return dt_input.strftime(format_string)


def parse_datetime(datetime_string: str, format_string: str | None = None) -> datetime:
    """Parse datetime string into timezone-aware datetime.

    Args:
        datetime_string: String to parse
        format_string: Format string (default: ISO format)

    Returns:
        Timezone-aware datetime in UTC
    """
    if format_string:
        dt_parsed = datetime.strptime(datetime_string, format_string)
    else:
        dt_parsed = datetime.fromisoformat(datetime_string)

    return ensure_aware(dt_parsed)


def get_age(birth_date: datetime | dt.datetime, today: datetime | dt.datetime | None = None) -> int:
    """Calculate age from birth date.

    Args:
        birth_date: Birth date
        today: Current date (for testing, defaults to utcnow())

    Returns:
        Age in years
    """
    today = today or utcnow()
    birth = ensure_aware(birth_date) if birth_date.tzinfo else birth_date

    age = (
        today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    )
    return age


def truncate_to_day(dt_input: datetime | dt.datetime) -> datetime:
    """Truncate datetime to start of day (00:00:00).

    Args:
        dt_input: Datetime to truncate

    Returns:
        Datetime truncated to start of day, preserving timezone
    """
    result = dt_input.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt_input.tzinfo is not None:
        return ensure_aware(result)
    return result


# Default datetime values for common use cases
ONE_DAY = timedelta(days=1)
ONE_WEEK = timedelta(weeks=1)
ONE_MONTH = timedelta(days=30)  # Approximate
ONE_YEAR = timedelta(days=365)  # Approximate


def get_default_session_expiry() -> datetime:
    """Get default session expiry time (7 days from now).

    Returns:
        Session expiry datetime
    """
    return add_timedelta(utcnow(), days=7)


def get_default_cache_expiry() -> datetime:
    """Get default cache expiry time (1 day from now).

    Returns:
        Cache expiry datetime
    """
    return add_timedelta(utcnow(), days=1)
