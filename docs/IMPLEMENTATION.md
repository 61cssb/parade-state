# Implementation Guide

**Version:** 1.0  
**Date:** 2026-05-08  
**Status:** Technical Implementation Guide  

---

## Table of Contents

1. [Development Setup](#1-development-setup)
2. [Testing Strategy](#2-testing-strategy)
3. [Database Implementation](#3-database-implementation)
4. [Code Organization](#4-code-organization)
5. [Build & Deployment](#5-build--deployment)

---

## 1. Development Setup

### 1.1 Environment Requirements

- Python 3.12+
- uv package manager
- Git

### 1.2 Project Initialization

```bash
# Clone repository
git clone <repository-url>
cd parade-state

# Install dependencies
uv sync

# Activate virtual environment (optional - uv handles this automatically)
source .venv/bin/activate  # On Linux/Mac
# or
.venv\Scripts\activate     # On Windows
```

### 1.3 Development Commands

```bash
# Run tests with coverage
uv run pytest

# Run tests with detailed output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_access_control.py

# Run tests matching pattern
uv run pytest -k "test_access_level"

# Start development server
uv run uvicorn src.parade_state.main:app --reload

# Run static analysis
uv run ruff check src/
uv run ruff format src/
```

### 1.4 Pre-commit Configuration

The project uses ruff for fast linting and formatting. Configure your editor to use ruff or run manually before commits.

---

## 2. Testing Strategy

### 2.1 Test Architecture

**Database Isolation:** Each test gets a completely fresh in-memory SQLite database with async support.

**Fixture Scope:**
- `test_db`: Function-scoped - new database per test
- `db_session`: Function-scoped - new session per test
- All sample data fixtures: Function-scoped - fresh data per test

### 2.2 Test Categories

**Current Test Suite:**
- `test_access_control.py` - Access level hierarchy, user access control, column visibility (8 tests)
- `test_csv_personnel.py` - Personnel identity, estab versioning, column mapping (10 tests)  
- `test_deployment_attendance.py` - Deployment lifecycle, session constraints, attendance rules (8 tests)

**Coverage:** 93.77% (target: 80%+)

### 2.3 Writing New Tests

**Pattern for isolated tests:**

```python
@pytest.mark.asyncio
async def test_your_feature(db_session, sample_deployment, sample_users):
    """Test description."""
    # Arrange: Set up test data using fixtures
    user = sample_users["admin"]
    deployment = sample_deployment
    
    # Act: Perform the operation being tested
    result = await your_function(deployment.id, user.id)
    
    # Assert: Verify expected behavior
    assert result.status == "expected_value"
```

**Key principles:**
- Each test should be completely independent
- Use provided fixtures rather than creating data manually
- Follow Arrange-Act-Assert pattern
- Test both success and failure cases

### 2.4 Test Fixtures

**Available fixtures:**

```python
# Database fixtures
db_session          # Async database session for each test
test_db             # Fresh database engine for each test

# Sample data fixtures (automatically create fresh data)
sample_access_levels    # Creates: unit, coy, platoon, section
sample_users            # Creates: admin user, regular user
sample_estab            # Creates: sample establishment
sample_personnel        # Creates: 3 sample personnel records
sample_deployment       # Creates: sample active deployment
```

**Using fixtures:**

```python
async def test_example(db_session, sample_users, sample_deployment):
    # Fixtures automatically provide fresh, isolated data
    admin = sample_users["admin"]
    deployment = sample_deployment
    
    # Test code here...
```

---

## 3. Database Implementation

### 3.1 Database Choice Rationale

**Production: PostgreSQL**
- Native UUID support
- JSONB for flexible schema evolution
- Partial unique indexes for business rules
- ACID compliance for data integrity
- Proven reliability at scale

**Testing: SQLite (in-memory)**
- Fast test execution
- Complete test isolation
- Async support via aiosqlite
- No external dependencies
- Cross-platform compatibility

### 3.2 Schema Management

**Current Status:** Models defined in SQLAlchemy ORM, but no Alembic migrations yet.

**Future Migration Path:**

```bash
# Initialize Alembic (when needed)
uv run alembic init migrations

# Generate migration from models
uv run alembic revision --autogenerate -m "Initial schema"

# Apply migrations
uv run alembic upgrade head

# Production database migration
DATABASE_URL=postgresql://... uv run alembic upgrade head
```

### 3.3 UUID Storage Implementation

**Cross-Database UUID Strategy:**

```python
# Base class (src/parade_state/db/__init__.py)
class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(
        String(36),              # String storage for SQLite compatibility
        primary_key=True,
        default=lambda: str(uuid.uuid4()),  # Generate as string
        index=True,
    )
```

**Usage in models:**

```python
# Foreign keys use String(36) for consistency
deployment_id: Mapped[str] = mapped_column(
    String(36), 
    ForeignKey("deployments.id", ondelete="CASCADE")
)
```

**PostgreSQL migration (when needed):**

```sql
-- Migrate String(36) to native UUID
ALTER TABLE users 
ALTER COLUMN id 
TYPE UUID 
USING id::UUID;

-- Repeat for all tables with UUID columns
```

### 3.4 JSON vs JSONB

**Implementation:**

```python
# Personnel.extra_fields uses JSON type
extra_fields: Mapped[dict] = mapped_column(JSON, default=dict)
```

**Behavior:**
- SQLite: Stores as JSON text, automatic serialization/deserialization
- PostgreSQL: Stores as JSONB for better query performance
- Application layer: Works with Python dicts seamlessly

**Future PostgreSQL optimization:**

```sql
-- Migrate JSON to JSONB for better performance
ALTER TABLE personnel 
ALTER COLUMN extra_fields 
TYPE JSONB 
USING extra_fields::JSONB;

-- Create GIN index for JSON queries
CREATE INDEX idx_personnel_extra_fields 
ON personnel USING GIN (extra_fields);
```

---

## 4. Code Organization

### 4.1 Project Structure

```
parade-state/
├── src/parade_state/
│   ├── __init__.py
│   ├── db/
│   │   └── __init__.py          # Database setup, Base class, session management
│   ├── models/
│   │   ├── __init__.py          # Model exports
│   │   ├── access.py            # User, AccessLevel, scopes
│   │   ├── attendance.py        # Session, AttendanceRecord
│   │   ├── audit.py             # AuditLog
│   │   ├── csv_ingestion.py     # Estab, CsvUpload, ColumnMapping
│   │   ├── deployment.py        # Deployment, overrides, notes
│   │   └── personnel.py         # Personnel
│   └── main.py                  # FastAPI app (to be implemented)
├── tests/
│   ├── conftest.py              # Pytest configuration and fixtures
│   ├── test_access_control.py   # Access control tests
│   ├── test_csv_personnel.py    # CSV and personnel tests
│   └── test_deployment_attendance.py  # Deployment tests
├── docs/
│   ├── SPECIFICATION.md         # Complete technical specification
│   ├── IMPLEMENTATION.md        # This file
│   └── ARCHITECTURE.md          # System architecture overview
├── pyproject.toml               # Project dependencies and configuration
└── uv.lock                      # Locked dependency versions
```

### 4.2 Model Organization

**Principles:**
- Each file contains a logical grouping of related models
- Models are organized by business domain, not technical concerns
- Foreign key relationships use string-based UUID references
- All models inherit from Base class for consistent UUID handling

**Adding new models:**

1. Create or update appropriate file in `src/parade_state/models/`
2. Import and add to `__init__.py` exports
3. Update relationships in related models
4. Add database constraints in `__table_args__`
5. Create tests in appropriate test file
6. Update documentation

### 4.3 Database Session Management

**Current pattern:**

```python
# In tests: use fixture-provided sessions
async def test_example(db_session):
    result = await db_session.execute(select(User))
    users = result.scalars().all()

# In application: use dependency injection (FastAPI)
async def get_users(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(User))
    return result.scalars().all()
```

**Session characteristics:**
- Async sessions throughout the stack
- expire_on_commit=False for better async performance
- Automatic cleanup via context managers

---

## 5. Build & Deployment

### 5.1 Local Development

**Development server:**

```bash
# Run with auto-reload
uv run uvicorn src.parade_state.main:app --reload --host 0.0.0.0 --port 8000
```

**Database setup (local PostgreSQL):**

```bash
# Using Docker for local PostgreSQL
docker run --name parade-state-postgres \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=parade_state \
  -p 5432:5432 \
  -d postgres:15

# Set environment variables
export DATABASE_URL="postgresql://postgres:password@localhost:5432/parade_state"
```

### 5.2 Production Deployment (Railway)

**Environment variables:**

```bash
DATABASE_URL           # Injected automatically by Railway Postgres add-on
SUPER_ADMIN_EMAIL      # Super admin email for bootstrap
GOOGLE_CLIENT_ID       # Google OAuth client ID
GOOGLE_CLIENT_SECRET   # Google OAuth client secret
SESSION_SECRET         # Session encryption secret
APP_BASE_URL           # https://{your-app}.railway.app
```

**Railway deployment:**

1. Push to main branch → Railway detects Python app via pyproject.toml
2. Installs dependencies via uv
3. Runs DB migrations (alembic upgrade head) as start command pre-step
4. Starts uvicorn

**Start command:**

```bash
uvicorn src.parade_state.main:app --host 0.0.0.0 --port $PORT
```

### 5.3 Static Analysis

**Run before commits:**

```bash
# Check code style and potential issues
uv run ruff check src/ tests/

# Format code automatically
uv run ruff format src/ tests/

# Check for type issues (when ruff type checking is fully enabled)
uv run ruff check --select TYP src/
```

**CI/CD Integration:**

```yaml
# Example GitHub Actions workflow
- name: Run ruff
  run: |
    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/
```

---

## 6. Performance Considerations

### 6.1 Database Query Optimization

**Current optimizations:**
- Indexed foreign keys for fast joins
- Indexed email for user login
- Indexed status fields for common queries
- Indexed dates for session lookups

**Future optimizations:**
- Add composite indexes for common query patterns
- Use database EXPLAIN ANALYZE to identify slow queries
- Consider read replicas for heavy read operations

### 6.2 Async Operations

**Benefits:**
- Non-blocking database operations
- Better concurrent request handling
- Efficient use of database connections

**Best practices:**
- Always use async/await for database operations
- Use connection pooling (configured in SQLAlchemy)
- Avoid N+1 queries with proper relationship loading

---

## 7. Troubleshooting

### 7.1 Common Development Issues

**Import errors:**
- Ensure you've run `uv sync` after pulling changes
- Check that PYTHONPATH includes `src/` directory

**Test failures:**
- Each test is independent - failures are self-contained
- Check that fixtures are being used correctly
- Verify database isolation by running tests individually

**Database connection issues:**
- Check DATABASE_URL is set correctly
- Verify PostgreSQL server is running
- Ensure database migrations are up to date

### 7.2 Debugging Tips

**Enable SQL logging:**

```python
# In tests, temporarily enable echo to see SQL queries
engine = create_async_engine(database_url, echo=True)
```

**Run single test:**

```bash
uv run pytest tests/test_specific.py::TestClass::test_function -v --tb=short
```

**Database inspection:**

```bash
# Connect to test database (add debug breakpoint)
import pdb; pdb.set_trace()

# Or use print statements for quick debugging
print(f"Result: {result}")
```

---

*End of Implementation Guide v1.0*
