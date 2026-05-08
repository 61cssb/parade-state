# Technical Implementation Decisions

**Version:** 1.0  
**Date:** 2026-05-07  
**Status:** Implementation Notes

---

## Overview

This document captures key technical decisions made during implementation that differ from or clarify the original data model specification.

---

## 1. Database Architecture

### Production vs Testing Databases

**Production:** PostgreSQL with native UUID support and JSONB types  
**Testing:** SQLite (in-memory) with async support via `aiosqlite`

**Rationale:**
- SQLite provides fast, isolated test execution
- PostgreSQL offers production-grade features (partial indexes, JSONB, native UUIDs)
- Application code abstracts database differences through SQLAlchemy

### UUID Storage Strategy

**Decision:** Store UUIDs as `String(36)` instead of native UUID types

**Implementation:**
```python
# Base class provides String-based UUID storage
class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
```

**Rationale:**
- SQLite doesn't support native UUID types
- String storage provides cross-database compatibility
- Application layer still uses Python `uuid.UUID` type for type safety
- PostgreSQL can still use UUID functions when needed via migrations

---

## 2. JSON Field Handling

### Personnel.extra_fields

**Decision:** Use SQLAlchemy `JSON` type instead of `Text`

**Implementation:**
```python
extra_fields: Mapped[dict] = mapped_column(JSON, default=dict)
```

**Rationale:**
- `JSON` type provides automatic serialization/deserialization
- Works with both SQLite (JSON as text) and PostgreSQL (JSONB)
- Allows Python dict manipulation without manual JSON encoding
- Maintains queryability for JSON contents in PostgreSQL

---

## 3. Constraint Enforcement Strategy

### Application-Level vs Database-Level Constraints

**Decision:** Enforce certain business rules at application level rather than database level

#### Active Deployment Constraint

**Original Spec:** Only one deployment can have `status = 'active'` (database constraint)  
**Implementation:** Application-level validation only

**Rationale:**
- SQLite doesn't support partial unique indexes (e.g., `WHERE status = 'active'`)
- PostgreSQL supports this, but maintaining divergent constraints increases complexity
- Application layer can provide better error messages and validation logic
- Allows multiple "draft" or "inactive" deployments without constraint violations

**Implementation Pattern:**
```python
# Application layer validation
async def create_deployment(status: str):
    if status == "active":
        existing = await get_active_deployment()
        if existing:
            raise BusinessLogicError("Only one active deployment allowed")
    # Create deployment...
```

#### Other Application-Level Constraints

- **Overlapping deployment validity ranges:** Application checks during creation
- **Session creation on inactive deployments:** Application validation
- **Cross-deployment data consistency:** Application-level transactions

---

## 4. Test Architecture

### Test Isolation Strategy

**Decision:** Fresh database for each test (function-scoped fixtures)

**Implementation:**
```python
@pytest.fixture
async def test_db():
    """Create a fresh test database for each test."""
    database_url = "sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(database_url, echo=False)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
    
    init_database(database_url)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield async_session_maker
    await engine.dispose()
```

**Benefits:**
- **Complete isolation:** No state leakage between tests
- **Reproducible results:** Tests can run in any order
- **Easy debugging:** Failures are self-contained
- **Parallel execution ready:** Safe to run tests in parallel

**Trade-offs:**
- Slightly slower execution (table recreation per test)
- Not an issue for current test suite size (26 tests run in ~2 seconds)

### Test Data Management

**Decision:** Simplified fixtures without caching logic

**Approach:**
- Each test creates exactly the data it needs
- No existence checks or fixture caching
- Explicit test data setup improves test clarity

**Before (cached fixtures):**
```python
@pytest.fixture
async def sample_access_levels(db_session: AsyncSession):
    # Check if access levels already exist
    stmt = select(AccessLevel).where(...)
    existing = await db_session.execute(stmt)
    if existing:
        return existing
    
    levels = [AccessLevel(...)]
    # ... creation logic
```

**After (fresh fixtures):**
```python
@pytest.fixture
async def sample_access_levels(db_session: AsyncSession):
    levels = [
        AccessLevel(name="unit", level_order=1),
        AccessLevel(name="coy", level_order=2),
        # ...
    ]
    for level in levels:
        db_session.add(level)
    await db_session.commit()
    return {level.name: level for level in levels}
```

---

## 5. Static Analysis Tooling

### Switch from mypy to ruff

**Decision:** Use ruff for both linting and type checking

**Rationale:**
- **Performance:** ruff is 10-100x faster than mypy
- **Unified tooling:** Single tool for linting, formatting, and type checking
- **Active development:** ruff has rapid development and Python 3.12+ support
- **Compatibility:** Works well with SQLAlchemy async patterns

**Configuration:**
```toml
[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings  
    "F",   # Pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    # ... other rules
]
```

---

## 6. Async Database Operations

### SQLAlchemy Async Integration

**Decision:** Use SQLAlchemy async throughout the stack

**Implementation:**
```python
# Database initialization
engine = create_async_engine(database_url, echo=False)
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Session usage
async with async_session_maker() as session:
    await session.execute(stmt)
    result = await session.scalar_one()
```

**Rationale:**
- Consistent async/await patterns throughout application
- Works with FastAPI's async request handling
- Better performance under concurrent load
- Required for NiceGUI async operations

---

## 7. Foreign Key Considerations

### String-Based Foreign Keys

**Decision:** Use `String(36)` for all foreign keys instead of UUID types

**Example:**
```python
deployment_id: Mapped[str] = mapped_column(
    String(36), 
    ForeignKey("deployments.id", ondelete="CASCADE")
)
```

**Rationale:**
- Consistent with primary key storage strategy
- Avoids type conversion issues between SQLite and PostgreSQL
- Application layer maintains type safety with Python UUID objects
- Database referential integrity still enforced

---

## 8. Future Migration Path

### PostgreSQL Migration Considerations

When migrating from SQLite test database to PostgreSQL production:

1. **UUID Storage:** Can migrate to native UUID columns
   ```sql
   ALTER TABLE users ALTER COLUMN id TYPE UUID USING id::UUID;
   ```

2. **JSON Fields:** Can upgrade to JSONB for better performance
   ```sql
   ALTER TABLE personnel ALTER COLUMN extra_fields TYPE JSONB USING extra_fields::JSONB;
   ```

3. **Partial Indexes:** Can add database-level constraints
   ```sql
   CREATE UNIQUE INDEX unique_active_deployment 
   ON deployments (status) 
   WHERE status = 'active';
   ```

4. **Constraint Migration:** Consider moving application-level constraints to database layer
   - Better data integrity guarantees
   - Improved performance (constraints enforced at database level)
   - Reduced application code complexity

---

## 9. Development Workflow

### Testing Best Practices Established

1. **Test Isolation:** Each test is completely independent
2. **Fixture Simplicity:** Avoid complex fixture caching logic
3. **Explicit Setup:** Test data creation is visible and clear
4. **Coverage Target:** Maintain 80%+ coverage (currently 93.77%)

### Code Quality Standards

1. **Static Analysis:** ruff for linting and type checking
2. **Test Coverage:** pytest with coverage reporting
3. **Async Safety:** All database operations use async/await patterns
4. **Type Safety:** Python type hints throughout, validated by ruff

---

*End of Technical Implementation Decisions*
