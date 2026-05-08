"""Datetime utilities for consistent datetime handling across the application.

This module provides centralized datetime handling to ensure:
- Consistent timezone handling (UTC)
- Database compatibility
- Easy maintenance and updates
- Type safety and predictability
"""

import datetime as dt
from datetime import datetime, timedelta, timezone
from typing import Optional, Union


# Constants for datetime handling
UTC = timezone.utc


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


def ensure_aware(dt_naive: Union[datetime, dt.datetime]) -> datetime:
    """Ensure a datetime is timezone-aware by adding UTC if naive.

    Args:
        dt_naive: Naive or aware datetime

    Returns:
        Timezone-aware datetime (UTC if was naive, original timezone if aware)
    """
    if dt_naive.tzinfo is None:
        return dt_naive.replace(tzinfo=UTC)
    return dt_naive


def ensure_naive(dt_aware: Union[datetime, dt.datetime]) -> datetime:
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


def to_utc(dt_input: Union[datetime, dt.datetime]) -> datetime:
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


def is_expired(expiry_time: Union[datetime, dt.datetime]) -> bool:
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
    start_time: Union[datetime, dt.datetime],
    end_time: Union[datetime, dt.datetime]
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

    if end_time.tzinfo is None:
        now_for_end = ensure_naive(now)
    else:
        now_for_end = ensure_aware(now)

    return start_time <= now_for_start <= end_time


def add_timedelta(
    base_time: Union[datetime, dt.datetime],
    **timedelta_kwargs
) -> datetime:
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
    dt_input: Union[datetime, dt.datetime],
    format_string: str = "%Y-%m-%d %H:%M:%S %Z"
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


def parse_datetime(
    datetime_string: str,
    format_string: Optional[str] = None
) -> datetime:
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


def get_age(birth_date: Union[datetime, dt.datetime]) -> int:
    """Calculate age from birth date.

    Args:
        birth_date: Birth date

    Returns:
        Age in years
    """
    today = utcnow()
    birth = ensure_aware(birth_date) if birth_date.tzinfo else birth_date

    age = today.year - birth.year - (
        (today.month, today.day) < (birth.month, birth.day)
    )
    return age


def truncate_to_day(dt_input: Union[datetime, dt.datetime]) -> datetime:
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
