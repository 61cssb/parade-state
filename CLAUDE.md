# Parade State Development Guide

**Purpose:** Primary reference for development patterns, conventions, and best practices when working on the Parade State application.

**What's Here:**
- Development patterns (utility modules, async operations, etc.)
- Code conventions and standards
- Testing patterns
- Performance and security guidelines

**What's Not Here:**
- System architecture and design decisions → See [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Detailed code style and formatting rules → See [CODE_STYLE.md](docs/CODE_STYLE.md)
- Specific utility module APIs → See module docstrings (e.g., `from parade_state.utils import utc_dt; help(utc_dt)`)
- API documentation → See FastAPI auto-generated docs at `/docs`

**How to Use This Guide:**
1. **Read [CODE_STYLE.md](docs/CODE_STYLE.md) first** - Understand code style and import conventions
2. Read through the patterns to understand the project's conventions
3. Follow the examples when implementing new features
4. Update this guide when introducing new patterns
5. Refer to specific sections when you need guidance on particular operations

## Development Patterns

### 0. Code Style First (⚠️ CRITICAL)

**🚨 STOP: Read [CODE_STYLE.md](docs/CODE_STYLE.md) before writing code!**

The most important pattern in this project is **utility module encapsulation**. This is strictly enforced:

```python
# ❌ NEVER DO THIS - Direct built-in imports
import datetime
import os
import uuid
from datetime import datetime, date

# ✅ ALWAYS DO THIS - Use utility modules
from parade_state.utils import utc_dt, env, ids

# For type annotations
def schedule_session(date: utc_dt.date) -> utc_dt.datetime:
    return utc_dt.utcnow()
```

**Why so strict?**
- One datetime bug (timezone confusion) caused a production incident
- Utility modules prevent entire classes of bugs
- Centralized logic = easier maintenance and testing
- Consistent behavior across the entire codebase

**Consequences of violations:**
- Code review will reject direct built-in imports
- Automated checks may flag violations
- You'll be asked to refactor the code

### 1. Utility Module Pattern

**Pattern:** Use centralized utility modules instead of native Python datatypes for common operations.

**Rationale:**
- Ensures consistency across the codebase
- Centralizes business logic (easier maintenance)
- Provides type-safe operations
- Eliminates common sources of bugs (timezone confusion, etc.)

**Example - Datetime Operations:**

❌ **Don't use native datetime directly:**
```python
from datetime import datetime, timedelta

now = datetime.utcnow()  # Deprecated and timezone-naive
expires = now + timedelta(days=7)  # Loses timezone information
if expires < datetime.utcnow():  # Fragile comparison
    pass
```

✅ **Do use utility modules:**
```python
from parade_state.utils import utc_dt

now = utc_dt.utcnow()  # Always UTC, always timezone-aware
expires = utc_dt.add_timedelta(now, days=7)  # Preserves timezone info
if utc_dt.is_expired(expires):  # Clear intent, handles edge cases
    pass
```

**Available Utility Modules:**
- `parade_state.utils.utc_dt` - UTC datetime operations (see module docstring for API reference)

**When to Create New Utility Modules:**
- You find yourself writing the same logic in multiple places
- The operation involves tricky edge cases (timezones, validation, etc.)
- You want to ensure consistent behavior across the codebase
- The operation would benefit from centralized testing

### 2. Async Database Operations

**Pattern:** Always use async database operations with FastAPI.

**Rationale:**
- Non-blocking I/O operations
- Better concurrent request handling
- Works seamlessly with FastAPI's async model
- Efficient database connection usage

❌ **Don't use sync database operations:**
```python
@router.get("/api/v1/users/{user_id}")
def get_user(user_id: str, db: AsyncSession = Depends(get_db_session)):
    user = db.get(User, user_id)  # Blocks the event loop
    return user
```

✅ **Do use async database operations:**
```python
@router.get("/api/v1/users/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
```

### 3. Dependency Injection for Database Sessions

**Pattern:** Always use FastAPI's dependency injection for database sessions.

**Rationale:**
- Automatic session cleanup
- Consistent with FastAPI patterns
- Easier testing (can override dependencies)
- Proper transaction management

❌ **Don't create sessions manually:**
```python
async def create_user(user_data: dict):
    async with get_db_session() as db:  # Bypasses FastAPI's system
        user = User(**user_data)
        db.add(user)
        await db.commit()
```

✅ **Do use dependency injection:**
```python
@router.post("/api/v1/users")
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db_session),  # Injected by FastAPI
):
    user = User(**user_data.dict())
    db.add(user)
    await db.commit()
    return user
```

### 4. Type Annotations

**Pattern:** Always provide complete type annotations.

**Rationale:**
- Better IDE support and autocomplete
- Catches type errors early
- Self-documenting code
- Required for FastAPI request/response validation

❌ **Don't omit type annotations:**
```python
async def create_user(email, name, db):  # What types are these?
    user = User(email=email, name=name)
    db.add(user)
    await db.commit()
    return user
```

✅ **Do include complete type annotations:**
```python
async def create_user(
    email: str,
    name: str,
    db: AsyncSession,
) -> User:
    user = User(email=email, name=name)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
```

### 5. Explicit Error Handling

**Pattern:** Use specific HTTP status codes and descriptive error messages.

**Rationale:**
- Better API consumer experience
- Easier debugging
- Clear API contract via OpenAPI docs

❌ **Don't use generic errors:**
```python
if not user:
    raise HTTPException(status_code=404, detail="Error")  # What error?
```

✅ **Do use specific, descriptive errors:**
```python
if not user:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="User not found",
    )
```

### 6. String UUID Storage

**Pattern:** Store UUIDs as strings in the database, use UUID objects for validation.

**Rationale:**
- SQLite compatibility (no native UUID type)
- Cross-database compatibility
- Easy string comparisons in queries

❌ **Don't use UUID objects for database queries:**
```python
user_id = uuid.UUID(user_id_str)  # Unnecessary conversion
result = await db.execute(select(User).where(User.id == user_id))
```

✅ **Do use strings for database operations:**
```python
# Validate UUID format if needed, but use string for queries
try:
    uuid.UUID(user_id)  # Just validation
except ValueError:
    raise HTTPException(status_code=400, detail="Invalid user ID format")

result = await db.execute(select(User).where(User.id == user_id))
```

## Code Conventions

### Import Organization

**Group imports in this order:**
1. Standard library imports
2. Third-party imports
3. Local application imports (from parade_state.*)

**Module-level imports preferred:**
```python
# ✅ Good - Module-level imports
from parade_state.utils import utc_dt
from parade_state.db import get_db_session

# Use explicit module calls
now = utc_dt.utcnow()
db = get_db_session()

# ❌ Avoid - Function-level imports (unless there's a naming conflict)
from parade_state.utils.utc_dt import utcnow, ensure_naive
```

### Naming Conventions

- **Modules:** `lowercase_with_underscores`
- **Classes:** `PascalCase`
- **Functions:** `lowercase_with_underscores`
- **Constants:** `UPPER_CASE_WITH_UNDERSCORES`
- **Private functions:** `_leading_underscore`

### Database Models

- **Table names:** `snake_case` (plural for tables)
- **Column names:** `snake_case`
- **Relationships:** Use `relationship()` with clear `back_populates`
- **Indexes:** Add indexes for frequently queried columns

### API Endpoints

- **Routes:** Use `/api/v1/{resource}` pattern
- **HTTP methods:** Use appropriate methods (GET for retrieval, POST for creation, etc.)
- **Status codes:** Use correct HTTP status codes
- **Responses:** Use Pydantic models for request/response validation

## Testing Patterns

### Test Organization

- **Unit tests:** `tests/test_{module}.py`
- **Integration tests:** `tests/test_{feature}.py`
- **Test fixtures:** `tests/conftest.py`

### Test Database

- Use in-memory SQLite for speed
- Isolate each test with fresh database
- Clean up after tests automatically

### Async Tests

- Use `@pytest.mark.asyncio` for async test functions
- Use `AsyncSession` for database operations
- Use `TestClient` with async dependency overrides

## Performance Considerations

### Database Queries

- Use `select()` instead of `all()` for large datasets
- Use `join()` strategically to avoid N+1 queries
- Add indexes for frequently queried columns
- Use bulk operations for multiple inserts/updates

### Memory Management

- Use generators instead of lists for large datasets
- Close database sessions properly
- Use connection pooling (configured in SQLAlchemy setup)

## Security Patterns

### Input Validation

- Always validate user input (use Pydantic models)
- Sanitize data before database operations
- Validate UUIDs and other ID formats

### Access Control

- Use dependency injection for authentication/authorization
- Check permissions at endpoint level
- Implement row-level security where appropriate

---

**Contributing:** When adding new development patterns, update this document to share knowledge with the team.

**See Also:** [ARCHITECTURE.md](docs/ARCHITECTURE.md) for system architecture and design decisions.