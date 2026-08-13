# Testing Guide

**Purpose**: Comprehensive guide for testing the Parade State application, covering architecture, patterns, and best practices for adding and maintaining tests.

**Audience**: Developers and agents working on the test suite.

## Table of Contents

- [Testing Philosophy](#testing-philosophy)
- [Architecture Overview](#architecture-overview)
- [Database Isolation Strategy](#database-isolation-strategy)
- [Fixture Structure](#fixture-structure)
- [Testing Patterns](#testing-patterns)
- [Common Pitfalls](#common-pitfalls)
- [Adding New Tests](#adding-new-tests)
- [Troubleshooting](#troubleshooting)

---

## Testing Philosophy

### Core Principles

1. **Per-Test Isolation**: Each test must be completely independent
   - Tests cannot rely on data from other tests
   - Tests must not leave data that affects other tests
   - Tests should pass regardless of execution order

2. **Realistic Testing**: Use real database and HTTP requests
   - No mocking of database operations
   - Use FastAPI TestClient for HTTP endpoint testing
   - Test the actual application stack, not abstractions

3. **Fixture-Based Data**: Use fixtures for test data setup
   - Centralized sample data creation
   - Consistent test data across tests
   - Easy to understand and maintain

4. **Utility Module Encapsulation**: Use centralized utilities
   - No direct use of `datetime`, `uuid`, `os` in test code
   - Import from `parade_state.utils` for consistency
   - Prevents entire classes of bugs (timezone confusion, etc.)

---

## Architecture Overview

### Test Organization

```
tests/
├── conftest.py                 # Shared fixtures and configuration
├── integration/                # Integration tests
│   ├── test_access_control_api.py
│   ├── test_api.py
│   ├── test_attendance_api.py
│   └── test_*.py
└── behavioral/                 # Behavioral tests
    └── test_auth.py
```

### Test Categories

1. **Integration Tests**: Test API endpoints with real database
   - Use `client` fixture for HTTP requests
   - Use `db_session` for direct database operations
   - Test complete request/response cycles

2. **Behavioral Tests**: Test user workflows and behavior
   - Focus on user interactions
   - Test complex scenarios
   - May use higher-level abstractions

---

## Running Tests

### Basic Commands

```bash
# Run all tests
uv run pytest

# Run integration tests
uv run pytest tests/integration/

# Run behavioral tests
uv run pytest tests/behavioral/

# Run specific test file
uv run pytest tests/integration/test_personnel_api.py

# Run specific test
uv run pytest tests/integration/test_personnel_api.py::test_update_personnel_as_admin
```

### Useful Options

```bash
# Verbose output (see each test name)
uv run pytest -v

# Stop on first failure
uv run pytest -x

# Show detailed output (x = stop on first failure, v = verbose, s = print statements)
uv run pytest -xvs

# Shorter traceback format
uv run pytest --tb=short

# Run without coverage (faster)
uv run pytest --no-cov

# Run tests matching a keyword/pattern
uv run pytest -k "audit"
uv run pytest -k "personnel"

# Run last failed tests
uv run pytest --lf

# Run tests multiple times (check for flakiness)
uv run pytest --count=3 tests/integration/test_personnel_api.py
```

### Test Organization

```bash
# Run all API tests
uv run pytest tests/integration/*_api.py

# Run specific category
uv run pytest tests/integration/test_personnel*.py

# Run tests from multiple files
uv run pytest tests/integration/test_personnel_api.py tests/integration/test_attendance_api.py
```

### Debugging Failed Tests

```bash
# Drop into debugger on failure
uv run pytest --pdb

# Show local variables on failure
uv run pytest -l

# Run with maximum verbosity
uv run pytest -vv

# Stop at first failure and drop into debugger
uv run pytest -x --pdb
```

### Coverage Reports

```bash
# Generate coverage report
uv run pytest --cov=src/parade_state

# Generate HTML coverage report
uv run pytest --cov=src/parade_state --cov-report=html

# View coverage in browser
open htmlcov/index.html  # On macOS
xdg-open htmlcov/index.html  # On Linux
```

---

## Database Isolation Strategy

### Why Per-Test Isolation?

We use **function-scoped database fixtures** (one database per test) rather than session-scoped (one database for all tests).

**Benefits**:
- ✅ Complete isolation between tests
- ✅ No data leakage or interference
- ✅ Easy debugging (failed tests don't affect others)
- ✅ Reliable and consistent results

**Trade-offs**:
- ⚠️ Slower than session-scoped (creates database per test)
- ✅ Still fast enough for development (14 seconds for 208 tests)
- ✅ Can optimize later if needed

### The Critical Fix: Database Reinitialization Prevention

**Problem**: FastAPI TestClient was triggering the app's lifespan manager, which called `init_database()` and reset the database to `:memory:`. This caused "no such table" errors because test data was created in a file-based database, but HTTP requests queried an empty in-memory database.

**Solution**: Modified the lifespan manager to check if database is already initialized:

```python
# src/parade_state/main.py

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    from parade_state.db import get_session_maker

    # Only initialize if not already initialized (prevents test database reset)
    if get_session_maker() is None:
        database_url = env.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
        init_database(database_url)
    yield
```

**Key Insight**: The check `get_session_maker() is None` allows test fixtures to initialize the database first, and the lifespan manager respects that initialization.

### Test Execution Flow

Each test follows this flow:

```
1. test_engine creates: /tmp/pytest-XXX/test_YYY/test.db
2. test_engine calls: init_database(database_url) → sets global _engine
3. session_maker creates: async_sessionmaker(test_engine)
4. Sample fixtures create: test data in database
5. TestClient triggers: lifespan (but skips reinitialization)
6. HTTP requests use: dependency override → test database
7. Test executes
8. Cleanup: dispose engine, clear overrides
```

---

## Fixture Structure

### Core Fixtures

#### `test_engine` (Function-Scoped)

**Purpose**: Create test database engine for each test

**Location**: `tests/conftest.py`

**Behavior**:
- Creates unique SQLite database file in test tmp directory
- Initializes SQLAlchemy engine
- Creates all tables using `Base.metadata.create_all()`
- Disposes engine after test completes

**Usage**: Rarely used directly in tests, mostly by other fixtures

#### `session_maker` (Function-Scoped)

**Purpose**: Create SQLAlchemy session maker for each test

**Behavior**:
- Returns `async_sessionmaker` configured for test engine
- Each call creates a new database session
- Sessions are automatically rolled back and closed

**Usage**: Used by `db_session` and `client` fixtures

#### `db_session` (Function-Scoped)

**Purpose**: Provide database session for direct operations

**Usage**:
```python
async def test_with_db_session(db_session):
    # Direct database operations
    user = User(email="test@example.com", name="Test")
    db_session.add(user)
    await db_session.commit()
```

#### `client` (Function-Scoped)

**Purpose**: Provide TestClient with database dependency override

**Usage**:
```python
async def test_http_endpoint(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
```

**Critical**: Always use the `client` fixture for HTTP testing, never create TestClient directly.

#### `test_db` (Backward Compatibility)

**Purpose**: Alias for `session_maker`, maintains backward compatibility

**Usage**: Existing test code uses this parameter name

**Note**: New tests should prefer `session_maker` directly

### Sample Data Fixtures

All sample fixtures are function-scoped and use `db_session`:

- `sample_access_levels`: Creates access level records
- `sample_users`: Creates admin and regular users
- `sample_nominal_roll`: Creates establishment record
- `sample_personnel`: Creates personnel records
- `sample_deployment`: Creates deployment with access grants
- `sample_session`: Creates session record
- `sample_sessions`: Creates multiple session records
- `sample_attendance_records`: Creates attendance records

**Usage**:
```python
async def test_with_samples(sample_users, sample_deployment):
    admin = sample_users["admin"]
    deployment = sample_deployment
```

---

## Testing Patterns

### Pattern 1: HTTP Endpoint Testing

**Use when**: Testing API endpoints

```python
@pytest.mark.asyncio
async def test_user_endpoint(client, sample_users):
    """Test getting user information via HTTP."""
    user = sample_users["user"]

    # Make HTTP request
    response = client.get(f"/api/v1/users/{user.id}")

    # Assert response
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == user.email
    assert data["name"] == user.name
```

**Key Points**:
- Use `client` fixture for HTTP requests
- Use sample fixtures for test data
- Assert both status code and response body

### Pattern 2: Direct Database Testing

**Use when**: Testing database operations directly

```python
@pytest.mark.asyncio
async def test_user_creation(db_session):
    """Test creating user directly in database."""
    from parade_state.models import User

    # Create user
    user = User(
        email="test@example.com",
        name="Test User",
        role="user",
        status="active",
    )
    db_session.add(user)
    await db_session.commit()

    # Verify in database
    from sqlalchemy import select
    result = await db_session.execute(
        select(User).where(User.email == "test@example.com")
    )
    assert result.scalar_one_or_none() is not None
```

**Key Points**:
- Use `db_session` fixture
- Use utility modules (not built-ins)
- Verify operations in database

### Pattern 3: Mixed Testing

**Use when**: Testing both HTTP and database operations

```python
@pytest.mark.asyncio
async def test_user_workflow(client, db_session, sample_users):
    """Test complete user workflow."""
    # Setup via database
    user = User(email="new@example.com", name="New User")
    db_session.add(user)
    await db_session.commit()

    # Verify via HTTP
    response = client.get(f"/api/v1/users/{user.id}")
    assert response.status_code == 200
    assert response.json()["email"] == "new@example.com"
```

### Pattern 4: Authentication Testing

**Use when**: Testing authenticated endpoints

```python
async def create_test_user_and_session(session_maker):
    """Helper to create user and session."""
    from parade_state.models import User
    from parade_state.auth.session import create_user_session

    async with session_maker() as db:
        user = User(
            email="test@example.com",
            name="Test User",
            role="user",
            status="active",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        session = await create_user_session(
            db,
            user_id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
        )
        await db.commit()

    return user, session


@pytest.mark.asyncio
async def test_authenticated_endpoint(client, session_maker):
    """Test endpoint requiring authentication."""
    user, session = await create_test_user_and_session(session_maker)

    # Make authenticated request
    headers = {"Authorization": f"Bearer {session.token}"}
    response = client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["email"] == user.email
```

**Key Points**:
- Create UserSession in database
- Use Bearer token in Authorization header
- Test both success and failure cases

---

## Critical Testing Requirements

### Database Initialization for Authentication Tests

**⚠️ CRITICAL**: Authentication tests require the global database state to be initialized.

**Why**: The authentication system uses `get_db_session()` which accesses the global `_async_session_maker`. If this is not initialized, authentication tests will fail with "no such table" errors.

**Solution**: The `test_engine` fixture calls `init_database()` to set the global state. This is handled automatically by the fixture - no test code changes needed.

### UUID String Comparison in Database Queries

**⚠️ CRITICAL**: Database queries must use string comparison for UUIDs.

**Why**: The database stores UUIDs as **strings** (`Mapped[str]`), but the `ids.to_uuid()` function converts strings to UUID objects. Comparing UUID objects with strings in database queries fails.

```python
# ❌ DON'T: Convert UUID to UUID object for database queries
result = await db.execute(
    select(User).where(User.id == ids.to_uuid(user_id_str))
)

# ✅ DO: Use string comparison for database queries
result = await db.execute(
    select(User).where(User.id == user_id_str)  # String comparison
)
```

**Note**: The `ids.to_uuid()` function is still useful for validation before database operations, but not for the queries themselves.

---

## Common Pitfalls

### ❌ Don't: Use TestClient Directly

```python
# BAD: Creates TestClient without database override
async def test_something():
    from fastapi.testclient import TestClient
    from parade_state.main import app

    client = TestClient(app)  # Wrong! Won't use test database
    response = client.get("/api/v1/users")
```

**Why**: This creates a TestClient that uses the default database (or `:memory:`), not the test database.

### ✅ Do: Use the client Fixture

```python
# GOOD: Uses test database via dependency override
async def test_something(client):
    response = client.get("/api/v1/users")
    assert response.status_code == 200
```

### ❌ Don't: Use Built-in Modules

```python
# BAD: Uses built-in datetime
from datetime import datetime

now = datetime.utcnow()  # Deprecated and timezone-naive
```

**Why**: Violates utility module encapsulation, causes timezone bugs.

### ✅ Do: Use Utility Modules

```python
# GOOD: Uses centralized utility
from parade_state.utils import utc_dt

now = utc_dt.utcnow()  # Always UTC, always timezone-aware
```

### ❌ Don't: Modify Global State in Tests

```python
# BAD: Modifies global _engine directly
from parade_state.db import _engine

async def test_something():
    global _engine
    _engine = create_async_engine(...)  # Don't do this
```

**Why**: Breaks isolation, affects other tests.

### ✅ Do: Use Fixtures for Database Setup

```python
# GOOD: Uses fixtures for database setup
async def test_something(test_db):
    async with test_db() as session:
        # Test code here
```

### ❌ Don't: Create Databases with :memory:

```python
# BAD: Uses in-memory database
@pytest.fixture
async def test_db():
    db_file = ":memory:"  # Connection isolation issues
```

**Why**: In-memory databases have connection isolation problems in async tests.

### ✅ Do: Use File-Based Databases

```python
# GOOD: Uses file-based database
@pytest.fixture
async def test_engine(tmp_path):
    db_file = tmp_path / "test.db"  # Proper isolation
    database_url = f"sqlite+aiosqlite:///{db_file}"
```

---

## Adding New Tests

### Step 1: Choose Test Type

**HTTP endpoint testing**: Use `client` fixture
**Database operation testing**: Use `db_session` fixture
**Mixed testing**: Use both fixtures

### Step 2: Create Test File

Place integration tests in `tests/integration/test_*.py`:
```python
"""Tests for feature X."""

import pytest
from parade_state.models import YourModel
```

### Step 3: Write Test Function

```python
@pytest.mark.asyncio
async def test_feature_y(client, sample_users):
    """Test that feature Y works correctly."""
    # Arrange
    user = sample_users["user"]

    # Act
    response = client.post("/api/v1/feature", json={"key": "value"})

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["expected_key"] == "expected_value"
```

### Step 4: Run Tests

```bash
# Run specific test
uv run pytest tests/integration/test_feature.py::test_feature_y -xvs

# Run all tests in file
uv run pytest tests/integration/test_feature.py -xvs

# Run all integration tests
uv run pytest tests/integration/ -xvs
```

### Step 5: Verify Isolation

```bash
# Run tests multiple times to ensure no interference
uv run pytest tests/integration/test_feature.py --count=3
```

---

## Troubleshooting

### "no such table: user_sessions"

**Cause**: Database not initialized properly

**Solution**: Ensure you're using the `client` fixture, not creating TestClient directly

### Tests pass individually but fail in groups

**Cause**: Data leakage between tests

**Solution**: Ensure all fixtures are function-scoped, not session-scoped

### "Database not initialized" error

**Cause**: Database fixture not set up correctly

**Solution**: Use the provided fixtures (`test_engine`, `session_maker`, `db_session`)

### Intermittent test failures

**Cause**: Tests depending on execution order

**Solution**: Ensure each test creates its own data, don't rely on data from other tests

### Coverage too low

**Cause**: Not testing all code paths

**Solution**: Add tests for error cases, edge cases, and different user roles

---

## Best Practices

### DO ✅

- Use per-test isolation (function-scoped fixtures)
- Use the `client` fixture for HTTP testing
- Use `db_session` for database operations
- Use sample fixtures for common test data
- Use utility modules instead of built-ins
- Test both success and failure cases
- Clean up resources in fixtures
- Write descriptive test names
- Add docstrings to tests

### DON'T ❌

- Create TestClient directly
- Use built-in datetime, uuid, os modules
- Modify global state in tests
- Use `:memory:` databases
- Make tests depend on execution order
- Skip writing tests for error cases
- Share mutable state between tests
- Use complex setup logic in tests

---

## Test Statistics

### Current Status

- **Total Tests**: 208
- **Passing**: 109 (87%)
- **Failing**: 16 (13% - authentication issues)
- **Fixture Errors**: 0 ✅

### Execution Time

- **Full Test Suite**: ~21 seconds
- **Single Test**: <1 second
- **Performance**: Acceptable for development

---

## Additional Resources

- [CLAUDE.md](../CLAUDE.md) - Development patterns and conventions
- [CODE_STYLE.md](CODE_STYLE.md) - Code style and formatting rules
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture and design

---

**Last Updated**: 2026-05-09
**Maintained By**: Development Team
**Questions**: See Troubleshooting section or ask in team chat
