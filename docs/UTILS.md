# Utility Modules Documentation

The `parade_state.utils` package provides centralized utilities for common operations to ensure consistency across the application.

## Available Utility Modules

### `utc_dt` - UTC Datetime Utilities

**Module:** `parade_state.utils.utc_dt`

**Purpose:** Centralized UTC datetime handling to avoid timezone confusion and ensure database compatibility.

**Import:**
```python
from parade_state.utils import utc_dt
```

**Key Functions:**

#### Time Retrieval
- `utc_dt.utcnow()` - Get current UTC datetime as timezone-aware datetime
- `utc_dt.utc_from_timestamp(timestamp)` - Convert timestamp to UTC datetime

#### Datetime Conversion
- `utc_dt.ensure_aware(dt_naive)` - Make naive datetime timezone-aware (assumes UTC)
- `utc_dt.ensure_naive(dt_aware)` - Convert aware datetime to naive (for DB operations)
- `utc_dt.to_utc(dt_input)` - Convert any datetime to UTC timezone

#### Time Calculations
- `utc_dt.add_timedelta(base_time, **kwargs)` - Add timedelta while preserving timezone awareness
- `utc_dt.is_expired(expiry_time)` - Check if expiry time has passed
- `utc_dt.is_valid_time_window(start_time, end_time)` - Check if current time is within window

#### Formatting & Parsing
- `utc_dt.format_datetime(dt_input, format_string)` - Format datetime as string
- `utc_dt.parse_datetime(datetime_string, format_string)` - Parse string to timezone-aware datetime

#### Time Constants
- `utc_dt.ONE_DAY`, `utc_dt.ONE_WEEK`, `utc_dt.ONE_MONTH`, `utc_dt.ONE_YEAR`

#### Helpers
- `utc_dt.get_age(birth_date)` - Calculate age from birth date
- `utc_dt.truncate_to_day(dt_input)` - Truncate datetime to start of day
- `utc_dt.get_default_session_expiry()` - Get default session expiry (7 days)
- `utc_dt.get_default_cache_expiry()` - Get default cache expiry (1 day)

**Usage Examples:**

```python
from parade_state.utils import utc_dt

# Get current UTC time
now = utc_dt.utcnow()

# Add 7 days while preserving timezone awareness
expires = utc_dt.add_timedelta(now, days=7)

# Check if session expired
if utc_dt.is_expired(session.expires_at):
    raise HTTPException(status_code=401, detail="Session expired")

# Store in database (use naive for SQLite compatibility)
session.expires_at = utc_dt.ensure_naive(expires)

# Retrieve and use in logic
if utc_dt.ensure_aware(session.expires_at) > utc_dt.utcnow():
    # Session is still valid
    pass
```

## Usage Guidelines

### When to Use Utility Modules

**Use `utc_dt` instead of native `datetime` when:**
- Getting current time
- Doing time arithmetic
- Checking expiration/validation
- Converting between timezones
- Formatting/parsing datetimes
- Working with database timestamps

**Direct `datetime` usage is acceptable for:**
- Type annotations
- Simple operations that don't involve timezones
- Third-party library integrations that require datetime objects

### Best Practices

1. **Always use module-level imports**
   ```python
   # ✅ Good
   from parade_state.utils import utc_dt
   now = utc_dt.utcnow()

   # ❌ Bad
   from parade_state.utils.utc_dt import utcnow
   now = utcnow()
   ```

2. **Be explicit about timezone handling**
   ```python
   # ✅ Good
   db_time = utc_dt.ensure_naive(utc_dt.utcnow())
   logic_time = utc_dt.ensure_aware(db_time)

   # ❌ Bad
   db_time = datetime.utcnow()  # Deprecated and timezone-unaware
   ```

3. **Use constants for time periods**
   ```python
   # ✅ Good
   expires = utc_dt.utcnow() + utc_dt.ONE_WEEK

   # ❌ Bad
   from datetime import timedelta
   expires = utc_dt.utcnow() + timedelta(days=7)
   ```

## Future Utility Modules

Potential additions to the utils package:
- **Validation utilities** - Email, phone number, etc.
- **Formatting utilities** - Currency, dates, etc.
- **String utilities** - Common string operations
- **Math utilities** - Military-specific calculations

## Contributing

When adding new utility modules:
1. Create a new file in `src/parade_state/utils/`
2. Add comprehensive docstrings
3. Include usage examples
4. Update this documentation
5. Add corresponding tests

For more information on application architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).