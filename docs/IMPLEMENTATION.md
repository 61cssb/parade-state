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
- `test_api.py` - General API endpoint tests (4 tests)
- `test_auth.py` - Authentication and session management tests (6 tests)
- `test_attendance_api.py` - Attendance management API tests (18 tests)
- `test_csv_personnel.py` - Personnel identity, estab versioning, column mapping (10 tests)
- `test_deployment_attendance.py` - Deployment lifecycle, session constraints, attendance rules (8 tests)
- `test_deployments_api.py` - Deployment management API tests (18 tests)
- `test_personnel_api.py` - Personnel management API tests (23 tests) ✨ NEW
- `test_sessions_api.py` - Session management API tests (21 tests)

**Total:** 133 tests, 100% pass rate ✨ UPDATED
**Coverage:** Improved with personnel API test coverage
**New:** Personnel API with deployment context, filtering, and search

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

## 3. API Implementation Status

### 3.1 Completed APIs

**Authentication & User Management (✅ Complete)**
- Google OAuth integration with callback handling
- User auto-registration and activation
- Role-based authorization (super_admin, admin, user)
- Session management with expiration and cleanup
- User CRUD operations with proper access control
- **Endpoints:** 7 authentication + 5 user management = 12 total

**Deployment Management (✅ Complete)**
- Deployment lifecycle (draft → active → inactive → closed → finalized)
- Personnel assignment overrides per deployment
- Deployment notes with version tracking
- Validity window enforcement
- Manual activation/deactivation
- **Endpoints:** 7 deployment management endpoints

**Attendance Session Management (✅ Complete)**
- AM/PM session creation and management
- Sequential status transitions (open → closed → finalized)
- Session uniqueness constraints (one per type per deployment per day)
- Proper validation and error handling
- **Endpoints:** 5 session management endpoints

**Attendance Management (✅ Complete)**
- Individual attendance recording (Create/Read/Update/Delete)
- Bulk attendance operations (create and update)
- Automatic snapshot functionality (deployment notes + personnel assignments)
- Retroactive edit detection and tracking
- Complete audit trail (created/updated/last_edit timestamps)
- Session status validation (open/closed/finalized)
- **Endpoints:** 8 attendance management endpoints

**Personnel Management (✅ Session 1 Complete)**
- Deployment-based personnel listing with filtering
- Personnel detail view with deployment context
- Unit hierarchy filtering (unit, sub_unit_1, sub_unit_2, sub_unit_3)
- Search functionality (name and service number)
- Personnel override awareness (shows effective assignments)
- Deployment notes integration
- Personnel update operations (admin only)
- Role-based access control (admin/super_admin/user)
- **Endpoints:** 3 personnel management endpoints
- **Tests:** 23 comprehensive tests

**Total API Endpoints:** 27 fully implemented and tested endpoints ✨ UPDATED

### 3.2 Next Phase: Personnel Detail View & Attendance History (🎯 NEXT)

**Why Attendance History Next?**
- Completes personnel management functionality
- Provides valuable insights for decision makers
- Foundation for reporting and analytics
- High user value for attendance tracking

**Completed Endpoints (Session 1):**
```python
# ✅ List personnel within a deployment context
GET /api/v1/personnel?deployment_id=xxx&unit=Alpha&sub_unit_1=1stPlatoon&search=John

# ✅ Get specific personnel record (shows deployment context)
GET /api/v1/personnel/{id}?deployment_id=xxx

# ✅ Update personnel record (admin only, within deployment context)
PATCH /api/v1/personnel/{id}?deployment_id=xxx
```

**Proposed Endpoints (Session 2):**
```python
# 🎯 Get personnel attendance history (within deployment)
GET /api/v1/personnel/{id}/attendance-history?deployment_id=xxx&date_from=xxx&date_to=xxx
```

### 3.3 Personnel API Implementation Details (Session 1)

**Architecture Overview:**
The Personnel API is built on the principle of deployment-scoped access, ensuring all personnel operations respect deployment boundaries and personnel overrides.

**Key Implementation Features:**

**1. Override-Aware Queries:**
```python
# Personnel overrides take precedence over base assignments
# Effective assignments = override data if exists, else base data
def get_effective_assignment(personnel, deployment_id):
    override = get_personnel_override(personnel.id, deployment_id)
    if override:
        return override.unit, override.sub_unit_1, override.sub_unit_2, override.sub_unit_3
    return personnel.unit, personnel.sub_unit_1, personnel.sub_unit_2, personnel.sub_unit_3
```

**2. Deployment Access Control:**
```python
# Verify user has access to deployment before querying personnel
async def verify_deployment_access(deployment_id, user_id, user_role, db):
    # Super admins have full access
    # Admins can access all deployments
    # Regular users need explicit deployment access (TODO)
    if user_role == "super_admin":
        return get_deployment(deployment_id)
    elif user_role == "admin":
        return get_deployment(deployment_id)
    else:
        raise HTTPException(403, "Insufficient permissions")
```

**3. Comprehensive Filtering:**
```python
# Filter by unit hierarchy with override awareness
query = select(Personnel).where(Personnel.estab_id == deployment.estab_id)

# Apply filters to effective assignments (overrides take precedence)
for personnel in personnel_list:
    effective_unit = override.unit if override else personnel.unit
    if filter_unit and effective_unit != filter_unit:
        continue  # Skip personnel not matching filter
```

**4. Search Functionality:**
```python
# Full-text search across name and service number
if search_term:
    query = query.where(
        or_(
            Personnel.full_name.ilike(f"%{search_term}%"),
            Personnel.pers_no.ilike(f"%{search_term}%")
        )
    )
```

**Database Performance Optimizations:**
- Composite indexes on frequently queried fields
- Efficient LEFT JOIN for override data
- Pagination support for large deployments
- Single-query deployment validation

**Testing Strategy:**
- 23 comprehensive tests covering all functionality
- Tests for override handling, filtering, search, and access control
- Edge cases (invalid deployment_id, different estab, etc.)
- Role-based access control testing

**Files Modified/Created:**
- `src/parade_state/api/personnel.py` - Main API implementation
- `src/parade_state/models/schemas.py` - Added PersonnelResponseWithDeployment
- `tests/test_personnel_api.py` - Comprehensive test suite
- `src/parade_state/main.py` - Integrated personnel router

**Key Features:**
- **Deployment-Scoped Querying:** All personnel operations within deployment context
- **Override Awareness:** Shows deployment-specific unit assignments (not base estab)
- **Filtering Capabilities:** By unit, subunit hierarchy, name, service number
- **Access Control:** Users can only see personnel in deployments they have access to
- **Attendance Integration:** Shows attendance history and current status
- **Search:** Full-text search across name and service number

**Implementation Priority:**
1. **Session 1:** Core deployment-based listing and filtering
2. **Session 2:** Personnel detail view and attendance history
3. **Session 3:** Personnel update operations and advanced filtering

**Future Phases:**
- **Advanced Access Control** (Phase 5): Deployment/subunit scope refinement **MOVED UP**
- **Reporting & Analytics** (Phase 6): Attendance summaries and trends **MOVED DOWN**
- **Performance & Scalability** (Phase 7): Database optimization and caching
- **Frontend Integration Support** (Phase 8): Mobile optimization and offline sync

**Implementation Priority:**
1. **Session 1:** ✅ Core deployment-based listing and filtering **COMPLETE**
2. **Session 2:** Personnel detail view and attendance history
3. **Session 3:** Personnel update operations and advanced filtering

**Why Access Control Before Reports:**
- Reports need proper deployment/subunit scoping to prevent unauthorized access
- Security foundation must be solid before exposing analytics
- Deployment-based access control ensures users only see relevant data
- Subunit scope filtering is essential for meaningful reports

### 3.3 Implementation Strategy: Deployment-Based Personnel API

**Session 1: Core Personnel Listing & Filtering**
- Create `src/parade_state/api/personnel.py`
- Implement `GET /api/v1/personnel` with deployment-based filtering
- Filter parameters: `deployment_id` (required), `unit`, `sub_unit_1`, `sub_unit_2`, `sub_unit_3`, `search`
- Integrate with deployment personnel overrides (show overridden assignments, not base)
- Add Pydantic schemas for personnel response models
- Implement basic access control (user must have deployment access)
- Write comprehensive tests (12-15 tests expected)

**Session 2: Personnel Detail View & History**
- Implement `GET /api/v1/personnel/{id}` with deployment context
- Show personnel details with deployment-specific assignments
- Implement `GET /api/v1/personnel/{id}/attendance-history`
- Filter attendance history by deployment and date range
- Add attendance summary statistics (present/absent/excused counts)
- Implement personnel search functionality
- Write tests for detail views and history (8-10 tests expected)

**Session 3: Personnel Update Operations**
- Implement `PATCH /api/v1/personnel/{id}` for admin-only updates
- Validate updates are within deployment context
- Add audit trail for personnel changes
- Implement advanced filtering and sorting
- Add pagination support for large result sets
- Performance optimization (database indexing)
- Complete test coverage (5-8 tests expected)

**Technical Considerations:**
- **Override Handling:** Query must join with `DeploymentPersonnelOverride` table
- **Access Control:** Check user's deployment access before returning personnel
- **Performance:** Add database indexes on `deployment_id`, `unit`, `sub_unit_*` fields
- **Search:** Use database `LIKE` or full-text search for name/service_number
- **Pagination:** Implement cursor-based pagination for large deployments

**Database Queries to Implement:**
```python
# Base query for deployment personnel (with overrides)
SELECT p.*, dop.unit as override_unit, dop.sub_unit_1 as override_sub_unit_1, ...
FROM personnel p
LEFT JOIN deployment_personnel_overrides dop
  ON dop.personnel_id = p.id AND dop.deployment_id = :deployment_id
WHERE p.estab_id = (SELECT estab_id FROM deployments WHERE id = :deployment_id)
  AND (p.unit = :filter_unit OR :filter_unit IS NULL)
  AND (p.full_name LIKE :search OR p.pers_no LIKE :search OR :search IS NULL)

# Attendance history query
SELECT ar.*, s.date, s.session_type
FROM attendance_records ar
JOIN sessions s ON s.id = ar.session_id
WHERE ar.personnel_id = :personnel_id
  AND s.deployment_id = :deployment_id
ORDER BY s.date DESC, s.session_type ASC
```

**Expected Outcomes:**
- **3 new API endpoints** for personnel management
- **25-33 new tests** for comprehensive coverage
- **Enhanced deployment roster** functionality for mobile UI
- **Foundation for reporting** and analytics features
- **Improved total test count:** ~135-140 tests

---

## 4. Database Implementation

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

## 5. Code Organization

### 4.1 Project Structure

```
parade-state/
├── src/parade_state/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py          # API router exports
│   │   ├── auth.py              # Authentication endpoints (Google OAuth, login/logout)
│   │   ├── users.py             # User management endpoints (CRUD, status changes)
│   │   ├── deployments.py       # Deployment management endpoints
│   │   ├── sessions.py          # Attendance session management endpoints
│   │   └── attendance.py        # Attendance record management endpoints
│   ├── db/
│   │   └── __init__.py          # Database setup, Base class, session management
│   ├── middleware/
│   │   └── auth.py              # Authentication middleware for protected endpoints
│   ├── models/
│   │   ├── __init__.py          # Model exports
│   │   ├── access.py            # User, AccessLevel, scopes
│   │   ├── attendance.py        # Session, AttendanceRecord
│   │   ├── audit.py             # AuditLog
│   │   ├── csv_ingestion.py     # Estab, CsvUpload, ColumnMapping
│   │   ├── deployment.py        # Deployment, overrides, notes
│   │   ├── personnel.py         # Personnel
│   │   └── schemas.py           # Pydantic request/response schemas
│   ├── utils/
│   │   ├── __init__.py          # Utility module exports
│   │   └── utc_dt.py            # UTC datetime utilities with timezone handling
│   ├── config.py                # Configuration management
│   ├── session.py               # Session management utilities
│   └── main.py                  # FastAPI application setup and router registration
├── tests/
│   ├── conftest.py              # Pytest configuration and fixtures
│   ├── test_access_control.py   # Access control tests (8 tests)
│   ├── test_api.py              # General API tests (4 tests)
│   ├── test_auth.py             # Authentication tests (6 tests)
│   ├── test_attendance_api.py   # Attendance API tests (18 tests)
│   ├── test_csv_personnel.py    # CSV and personnel tests (10 tests)
│   ├── test_deployment_attendance.py  # Deployment tests (8 tests)
│   ├── test_deployments_api.py  # Deployment API tests (18 tests)
│   └── test_sessions_api.py     # Session API tests (21 tests)
├── docs/
│   ├── SPECIFICATION.md         # Complete technical specification
│   ├── IMPLEMENTATION.md        # This file
│   ├── ARCHITECTURE.md          # System architecture overview
│   └── NEXT_PHASE.md            # Next phase planning and roadmap
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

## 6. Build & Deployment

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

## 7. Performance Considerations

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

## 8. Troubleshooting

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
