# Parade State Development Guide

**Purpose:** Primary reference for development patterns and conventions when working on the Parade State application.

**What's Here:**
- Critical development patterns (what you need every session)
- Essential coding standards
- Quick testing reference

**What's Not Here:**
- System architecture and design decisions → See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Detailed code style and formatting rules → See [docs/CODE_STYLE.md](docs/CODE_STYLE.md)
- Performance optimization guidelines → See [docs/PERFORMANCE.md](docs/PERFORMANCE.md)
- Security patterns and best practices → See [docs/SECURITY.md](docs/SECURITY.md)
- Specific utility module APIs → See module docstrings (e.g., `from parade_state.utils import utc_dt; help(utc_dt)`)
- API documentation → See FastAPI auto-generated docs at `/docs`

**How to Use This Guide:**
1. **Read [docs/CODE_STYLE.md](docs/CODE_STYLE.md) first** - Understand code style and import conventions
2. Follow these patterns for every feature you implement
3. Refer to detailed docs for task-specific guidance
4. Update this guide when introducing critical new patterns

---

## Development Patterns

### 0. Code Style First (⚠️ CRITICAL)

**🚨 STOP: Read [docs/CODE_STYLE.md](docs/CODE_STYLE.md) before writing code!**

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

### 1. Async Database Operations

**Pattern:** Always use async database operations with FastAPI.

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

### 2. Dependency Injection for Database Sessions

**Pattern:** Always use FastAPI's dependency injection for database sessions.

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

### 3. Type Annotations

**Pattern:** Always provide complete type annotations.

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

### 4. Explicit Error Handling

**Pattern:** Use specific HTTP status codes and descriptive error messages.

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

### 5. String UUID Storage

**Pattern:** Store UUIDs as strings in the database, use UUID objects for validation.

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

---

## Testing Patterns

**🚨 STOP: Read [docs/TESTING.md](docs/TESTING.md) before writing tests!**

### Running Tests

**Essential Commands:**
```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/integration/test_personnel_api.py

# Run specific test
uv run pytest tests/integration/test_personnel_api.py::test_update_personnel_as_admin

# Run with verbose output
uv run pytest -v

# Stop on first failure
uv run pytest -x

# Run without coverage (faster)
uv run pytest --no-cov

# Run tests matching pattern
uv run pytest -k "personnel"
```

**For more testing options and detailed guidance, see [docs/TESTING.md](docs/TESTING.md)**

### Quick Testing Reference

- **Integration tests:** `tests/integration/test_*.py`
- **Test fixtures:** `tests/conftest.py`
- **Database:** File-based SQLite (not `:memory:`) for proper isolation
- **Async tests:** Use `@pytest.mark.asyncio`
- **HTTP testing:** Use `client` fixture, never create TestClient directly

---

## Additional Guidelines

For detailed guidance on specific topics, refer to these documents:

- **[docs/CODE_STYLE.md](docs/CODE_STYLE.md)** - Complete code style and formatting reference
- **[docs/TESTING.md](docs/TESTING.md)** - Comprehensive testing guide
- **[docs/PERFORMANCE.md](docs/PERFORMANCE.md)** - Performance optimization guidelines
- **[docs/SECURITY.md](docs/SECURITY.md)** - Security patterns and best practices
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System architecture and design decisions

---

**Contributing:** When adding new development patterns, update this document to share knowledge with the team.

**See Also:** [docs/TESTING.md](docs/TESTING.md) for testing patterns and [docs/CODE_STYLE.md](docs/CODE_STYLE.md) for code style conventions.
