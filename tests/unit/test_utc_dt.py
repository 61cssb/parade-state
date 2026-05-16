"""Unit tests for utc_dt utility module."""

from datetime import datetime, timedelta, timezone

from parade_state.utils import utc_dt


class TestTimeRetrieval:
    """Test time retrieval functions."""

    def test_utcnow_returns_timezone_aware(self):
        """Test that utcnow returns timezone-aware datetime."""
        now = utc_dt.utcnow()
        assert now.tzinfo is not None
        assert now.tzinfo == timezone.utc

    def test_utcnow_is_recent(self):
        """Test that utcnow returns current time."""
        now = utc_dt.utcnow()
        # Should be very recent (within 1 second)
        time_diff = abs((datetime.now(timezone.utc) - now).total_seconds())
        assert time_diff < 1.0

    def test_utc_from_timestamp(self):
        """Test converting timestamp to UTC datetime."""
        # Unix epoch: January 1, 1970, 00:00:00 UTC
        timestamp = 0
        result = utc_dt.utc_from_timestamp(timestamp)
        assert result.tzinfo == timezone.utc
        assert result.year == 1970
        assert result.month == 1
        assert result.day == 1

    def test_utc_from_timestamp_with_value(self):
        """Test converting specific timestamp to UTC datetime."""
        # January 1, 2020, 12:00:00 UTC = 1577880000
        timestamp = 1577880000
        result = utc_dt.utc_from_timestamp(timestamp)
        assert result.tzinfo == timezone.utc
        assert result.year == 2020
        assert result.month == 1
        assert result.day == 1
        assert result.hour == 12


class TestConversion:
    """Test datetime conversion functions."""

    def test_ensure_aware_with_naive_datetime(self):
        """Test ensure_aware adds UTC to naive datetime."""
        naive_dt = datetime(2020, 1, 1, 12, 0, 0)
        aware_dt = utc_dt.ensure_aware(naive_dt)
        assert aware_dt.tzinfo == timezone.utc
        assert aware_dt.replace(tzinfo=None) == naive_dt

    def test_ensure_aware_with_aware_datetime(self):
        """Test ensure_aware preserves existing timezone."""
        aware_dt = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = utc_dt.ensure_aware(aware_dt)
        assert result == aware_dt
        assert result.tzinfo == timezone.utc

    def test_ensure_naive_with_aware_datetime(self):
        """Test ensure_naive removes timezone info."""
        aware_dt = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        naive_dt = utc_dt.ensure_naive(aware_dt)
        assert naive_dt.tzinfo is None
        assert naive_dt == aware_dt.replace(tzinfo=None)

    def test_ensure_naive_with_naive_datetime(self):
        """Test ensure_naive preserves naive datetime."""
        naive_dt = datetime(2020, 1, 1, 12, 0, 0)
        result = utc_dt.ensure_naive(naive_dt)
        assert result == naive_dt
        assert result.tzinfo is None

    def test_to_utc_with_naive_datetime(self):
        """Test to_utc assumes naive datetimes are UTC."""
        naive_dt = datetime(2020, 1, 1, 12, 0, 0)
        result = utc_dt.to_utc(naive_dt)
        assert result.tzinfo == timezone.utc
        assert result.replace(tzinfo=None) == naive_dt

    def test_to_utc_with_aware_datetime(self):
        """Test to_utc converts aware datetime to UTC."""
        # Create datetime in UTC+5
        import datetime as dt

        tz_plus_5 = dt.timezone(dt.timedelta(hours=5))
        aware_dt = datetime(2020, 1, 1, 17, 0, 0, tzinfo=tz_plus_5)
        result = utc_dt.to_utc(aware_dt)
        assert result.tzinfo == timezone.utc
        assert result.hour == 12  # 17:00 in UTC+5 = 12:00 UTC


class TestExpiration:
    """Test expiration checking functions."""

    def test_is_expired_with_past_time(self):
        """Test is_expired returns True for past times."""
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        assert utc_dt.is_expired(past_time) is True

    def test_is_expired_with_future_time(self):
        """Test is_expired returns False for future times."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        assert utc_dt.is_expired(future_time) is False

    def test_is_expired_with_naive_past_time(self):
        """Test is_expired handles naive past datetime."""
        past_time = datetime(2020, 1, 1, 12, 0, 0)
        assert utc_dt.is_expired(past_time) is True

    def test_is_expired_with_naive_future_time(self):
        """Test is_expired handles naive future datetime."""
        future_time = datetime(2030, 1, 1, 12, 0, 0)
        assert utc_dt.is_expired(future_time) is False

    def test_is_valid_time_window_currently_valid(self):
        """Test is_valid_time_window returns True when current time is in window."""
        start_time = datetime.now(timezone.utc) - timedelta(hours=1)
        end_time = datetime.now(timezone.utc) + timedelta(hours=1)
        assert utc_dt.is_valid_time_window(start_time, end_time) is True

    def test_is_valid_time_window_not_yet_started(self):
        """Test is_valid_time_window returns False when window hasn't started."""
        start_time = datetime.now(timezone.utc) + timedelta(hours=1)
        end_time = datetime.now(timezone.utc) + timedelta(hours=2)
        assert utc_dt.is_valid_time_window(start_time, end_time) is False

    def test_is_valid_time_window_already_ended(self):
        """Test is_valid_time_window returns False when window has ended."""
        start_time = datetime.now(timezone.utc) - timedelta(hours=2)
        end_time = datetime.now(timezone.utc) - timedelta(hours=1)
        assert utc_dt.is_valid_time_window(start_time, end_time) is False

    def test_is_valid_time_window_with_naive_datetimes(self):
        """Test is_valid_time_window handles naive datetimes."""
        start_time = datetime(2020, 1, 1, 0, 0, 0)
        end_time = datetime(2030, 1, 1, 0, 0, 0)
        # Current time should be within this window
        assert utc_dt.is_valid_time_window(start_time, end_time) is True


class TestTimedelta:
    """Test timedelta operations."""

    def test_add_timedelta_with_aware_datetime(self):
        """Test add_timedelta preserves timezone awareness."""
        base_time = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = utc_dt.add_timedelta(base_time, days=1)
        assert result.tzinfo == timezone.utc
        assert result.day == 2
        assert result.month == 1
        assert result.year == 2020

    def test_add_timedelta_with_naive_datetime(self):
        """Test add_timedelta works with naive datetime."""
        base_time = datetime(2020, 1, 1, 12, 0, 0)
        result = utc_dt.add_timedelta(base_time, days=1)
        assert result.tzinfo is None
        assert result.day == 2

    def test_add_timedelta_with_multiple_units(self):
        """Test add_timedelta with multiple time units."""
        base_time = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = utc_dt.add_timedelta(base_time, days=1, hours=2, minutes=30)
        assert result.day == 2
        assert result.hour == 14
        assert result.minute == 30

    def test_add_timedelta_with_negative_values(self):
        """Test add_timedelta with negative values."""
        base_time = datetime(2020, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        result = utc_dt.add_timedelta(base_time, days=-1)
        assert result.day == 1


class TestFormatting:
    """Test datetime formatting and parsing."""

    def test_format_datetime_with_aware_datetime(self):
        """Test format_datetime with timezone-aware datetime."""
        dt = datetime(2020, 1, 1, 12, 30, 45, tzinfo=timezone.utc)
        result = utc_dt.format_datetime(dt, "%Y-%m-%d %H:%M:%S")
        assert result == "2020-01-01 12:30:45"

    def test_format_datetime_with_naive_datetime(self):
        """Test format_datetime converts naive to aware."""
        dt = datetime(2020, 1, 1, 12, 30, 45)
        result = utc_dt.format_datetime(dt, "%Y-%m-%d %H:%M:%S")
        assert result == "2020-01-01 12:30:45"

    def test_parse_datetime_with_iso_format(self):
        """Test parse_datetime with ISO format string."""
        dt_string = "2020-01-01T12:30:45"
        result = utc_dt.parse_datetime(dt_string)
        assert result.tzinfo == timezone.utc
        assert result.year == 2020
        assert result.month == 1
        assert result.day == 1
        assert result.hour == 12
        assert result.minute == 30
        assert result.second == 45

    def test_parse_datetime_with_custom_format(self):
        """Test parse_datetime with custom format."""
        dt_string = "2020-01-01 12:30:45"
        result = utc_dt.parse_datetime(dt_string, "%Y-%m-%d %H:%M:%S")
        assert result.tzinfo == timezone.utc
        assert result.year == 2020
        assert result.month == 1


class TestHelpers:
    """Test helper functions."""

    def test_get_age_with_past_birth_date(self):
        """Test get_age calculates correct age."""
        # Use exact date calculation to avoid leap year issues
        today = datetime.now(timezone.utc)
        birth_date = datetime(
            today.year - 30, today.month, today.day, tzinfo=timezone.utc
        )
        age = utc_dt.get_age(birth_date)
        assert age == 30

    def test_get_age_with_naive_birth_date(self):
        """Test get_age handles naive birth date."""
        birth_date = datetime(1990, 1, 1, 0, 0, 0)
        age = utc_dt.get_age(birth_date)
        # Age should be approximately correct (within 1 year)
        current_year = datetime.now(timezone.utc).year
        assert age in [current_year - 1990 - 1, current_year - 1990]

    def test_get_age_before_birthday_this_year(self):
        """Test get_age before birthday in current year."""
        # Test the specific scenario: June 1, 2020 with birthday Dec 31, 1990
        birth_date = datetime(1990, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
        test_date = datetime(2020, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        age = utc_dt.get_age(birth_date, today=test_date)
        assert age == 29  # Haven't had birthday yet in 2020

    def test_truncate_to_day_with_aware_datetime(self):
        """Test truncate_to_day preserves timezone."""
        dt = datetime(2020, 1, 1, 15, 30, 45, tzinfo=timezone.utc)
        result = utc_dt.truncate_to_day(dt)
        assert result.tzinfo == timezone.utc
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0
        assert result.day == 1

    def test_truncate_to_day_with_naive_datetime(self):
        """Test truncate_to_day works with naive datetime."""
        dt = datetime(2020, 1, 1, 15, 30, 45)
        result = utc_dt.truncate_to_day(dt)
        assert result.tzinfo is None
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0


class TestDefaultValues:
    """Test default value constants and functions."""

    def test_one_day_constant(self):
        """Test ONE_DAY constant."""
        assert utc_dt.ONE_DAY == timedelta(days=1)

    def test_one_week_constant(self):
        """Test ONE_WEEK constant."""
        assert utc_dt.ONE_WEEK == timedelta(weeks=1)

    def test_one_month_constant(self):
        """Test ONE_MONTH constant."""
        assert utc_dt.ONE_MONTH == timedelta(days=30)

    def test_one_year_constant(self):
        """Test ONE_YEAR constant."""
        assert utc_dt.ONE_YEAR == timedelta(days=365)

    def test_get_default_session_expiry(self):
        """Test get_default_session_expiry returns 7 days from now."""
        expiry = utc_dt.get_default_session_expiry()
        now = utc_dt.utcnow()
        time_diff = (expiry - now).total_seconds()
        # Should be approximately 7 days (within 1 second tolerance)
        expected_seconds = 7 * 24 * 60 * 60
        assert abs(time_diff - expected_seconds) < 1.0

    def test_get_default_cache_expiry(self):
        """Test get_default_cache_expiry returns 1 day from now."""
        expiry = utc_dt.get_default_cache_expiry()
        now = utc_dt.utcnow()
        time_diff = (expiry - now).total_seconds()
        # Should be approximately 1 day (within 1 second tolerance)
        expected_seconds = 24 * 60 * 60
        assert abs(time_diff - expected_seconds) < 1.0
