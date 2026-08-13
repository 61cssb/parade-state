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

**Database Isolation:** Each test gets a completely fresh file-based SQLite database with async support.

**Rationale for File-Based Database:**
- **Proper isolation**: File-based databases avoid connection isolation issues with async SQLite
- **Transaction safety**: Each test gets its own database file preventing interference
- **Debugging**: Database files persist temporarily for debugging failed tests
- **Performance**: Slightly slower than `:memory:`, but provides reliable test isolation

**Fixture Scope:**
- `test_engine`: Function-scoped - creates engine and database file per test
- `session_maker`: Function-scoped - creates session factory per test
- `db_session`: Function-scoped - new session per test
- `client`: Function-scoped - creates TestClient with dependency override per test
- All sample data fixtures: Function-scoped - fresh data per test

**Critical Pattern:** Tests initialize the global database state via `init_database()` to ensure the authentication system works correctly.

### 2.2 Test Categories

**Current Test Suite:**
- `tests/integration/test_access_control_api.py` - Access level hierarchy, user access control, column visibility (19 tests)
- `tests/integration/test_api.py` - Authentication, user management, role management (18 tests)
- `tests/integration/test_attendance_api.py` - Attendance management, snapshots, constraints (40 tests)
- `tests/integration/test_csv_upload_api.py` - CSV upload pipeline, hash dedup, mapping (9 tests)
- `tests/integration/test_deferments_api.py` - Deferment CRUD, callup_status transitions, super_admin auth (15 tests)
- `tests/integration/test_deployments_api.py` - Deployment lifecycle, CRUD operations (18 tests)
- `tests/integration/test_deployment_exclusions_api.py` - Personnel exclusion management (9 tests)
- `tests/integration/test_nominal_rolls_api.py` - Nominal Roll lifecycle (confirm/unconfirm/delete, label updates) (18 tests)
- `tests/integration/test_personnel_api.py` - Personnel management, search, filtering (12 tests)
- `tests/integration/test_personnel_attendance_history.py` - Personnel attendance history and statistics (NR/Tagging-scoped, AM/PM slots)
- `tests/integration/test_sessions_410.py` - Sessions endpoints return 410 Gone (sessions removed in issue #4)
- `tests/integration/test_users_api.py` - User CRUD, role/status transitions (3 tests)
- `tests/integration/test_audit_api.py` - Audit log filtering and pagination (10 tests)

**Total:** 292 tests, 100% pass rate ✅ UPDATED
**Coverage:** Comprehensive integration test coverage across all major features
**Performance:** ~23 seconds for full integration test suite

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

**Core fixtures:**

```python
test_engine     # Creates database engine and file, initializes global state
session_maker   # Creates session factory for test database
db_session       # Provides database session for direct operations
client          # Provides TestClient with database dependency override
test_db          # Alias for session_maker (backward compatibility)

# Sample data fixtures (automatically create fresh data)
sample_access_levels    # Creates: unit, coy, platoon, section
sample_users            # Creates: admin user, regular user
sample_nominal_roll            # Creates: sample establishment
sample_personnel        # Creates: 3 sample personnel records
sample_deployment       # Creates: sample active deployment
sample_sessions         # Creates: multiple session records
sample_attendance_records  # Creates: attendance records
```

**Using fixtures:**

```python
async def test_example(client, sample_users, sample_deployment):
    # Fixtures automatically provide fresh, isolated data
    admin = sample_users["admin"]
    deployment = sample_deployment

    # HTTP endpoint testing
    response = client.get(f"/api/v1/deployments/{deployment.id}")
    assert response.status_code == 200
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
- **Deployment date editing:** Admin UI supports editing valid_from/valid_until via an inline form; API validates that no sessions fall outside the new date range
- **Admin deployments page:** Auto-expands active deployment on page load; per-session "Update" button linking to /attendance; autofills next session date/type
- **Endpoints:** 7 deployment management endpoints

**Attendance Session Management (🗑 Removed in issue #4)**
- The user-managed `Session` model (open/closed/finalized) has been removed.
- AM and PM are now hardcoded slots on a single `Attendance` row per person/day.
- `/api/v1/sessions/*` routes return 410 Gone as signposts.
- Historical reporting views that depended on sessions are broken (see issue #4
  "Out of scope") and need separate consideration.

**Attendance Management (✅ Reworked in issue #4 PR 1)**
- NR/Tagging-scoped attendance: one `Attendance` row per `(personnel, date)`
  carrying `status_am`/`remarks_am` and `status_pm`/`remarks_pm`.
- Active-scope gating: a super-admin activates an `AttendanceScope` per NR
  (NR itself or a Tagging) before attendance can be recorded.
- Bulk upsert endpoint (`PUT /api/v1/attendance/upsert`) with snapshot capture.
- "Copy Remarks" endpoint (`POST /api/v1/attendance/copy-remarks`): before 12pm
  copies previous day's `remarks_pm` → today's `remarks_am`; after 12pm copies
  today's `remarks_am` → `remarks_pm`.
- Tagging delete guarded (409) when linked to attendance or set as active scope.
- Attendance status enum: present, absent, time_off, mc, yet_to_inpro, outpro,
  reporting_sick, late, att_out (default: absent).
- **Forthcoming (PR 3):** admin attendance page with scope-activation UI and
  Copy Remarks button.

**Subunit-1 Attendance Access (✅ Reworked in issue #4 PR 2)**
- New `UserSubunitAssignment(user_id, nominal_roll_id, sub_unit_1)` model —
  grants a user attendance-update rights for one sub_unit_1 on one NR.
- Server-enforced 403 on `PUT /api/v1/attendance/upsert` and
  `POST /api/v1/attendance/copy-remarks` when the caller lacks an assignment
  for a target personnel's effective sub_unit_1. Effective sub_unit_1 follows
  the active Tagging overlay's `to_sub_unit_1` (tagging-aware), falling back
  to the personnel's canonical `sub_unit_1`.
- `super_admin` bypasses entirely; **deny-by-default** (no assignments = 403).
- Super-admin CRUD API:
  `POST /api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments`,
  `DELETE .../subunit-assignments/{assignment_id}`,
  `GET .../nominal-rolls/{nr_id}/subunit-assignments`,
  `GET .../users/{user_id}/subunit-assignments`.
- Migration `k1f2a3b4c5d6`. 332 tests passing.

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

**Deferments (✅ Super-admin MVP)**
- Personnel deferment CRUD linked to a single nominal roll personnel record
- `rank_name` and `sub_unit` snapshotted at creation from the linked personnel
- Reason enum (12 values) and status enum (8 values)
- Personnel `callup_status` field (`Called Up` / `Not Called Up` / `Deferred`):
  - Approved deferment → `Deferred`
  - Reverting from Approved to a non-neutral status → `Called Up`
  - `Not called up` / `Do not call up` deferment statuses are neutral (no callup change)
  - Deleting an Approved deferment reverts to `Called Up`
- Super-admin-only: API and admin UI enforce `role == "super_admin"`
- Admin UI under `/admin/deferments` (nav link gated by super_admin role)
- **Endpoints:** 5 deferment endpoints under `/api/v1/deferments`
- **Tests:** 15 behavioral tests

**Taggings (✅ Super-admin MVP)**
- Tagging overlay CRUD: a named overlay of person → subunit remappings on a
  single Nominal Roll. Taggings never mutate the underlying NR's personnel
  or subunit data — downstream views (attendance / groupings, issues #4/#5)
  consume the remapped structure from here.
- Two entities: `Tagging` (globally-unique label, NR FK CASCADE, audit fields)
  and `TaggingEntry` (one remap per person per tagging; 4-string `from_*` /
  `to_*` subunit tuple mirroring `DeploymentPersonnelOverride`).
- `from_*` auto-snapshotted from the linked personnel when omitted at
  create/edit time.
- Clone-to-NR: `POST /api/v1/taggings/{id}/clone` matches source personnel
  to target-NR rows by `Personnel.short_id` (the cross-roll person
  identifier); unmatched source personnel are surfaced in the response.
- Label uniqueness is server-enforced (409 on duplicate). Personnel must
  belong to the parent tagging's NR (400 on cross-NR contamination).
- Super-admin-only: API and admin UI enforce `role == "super_admin"`.
- Admin UI under `/admin/taggings` (nav link gated by super_admin role) with
  create/edit (per-person remap picker) and clone modals.
- **Endpoints:** 6 tagging endpoints under `/api/v1/taggings`
- **Tests:** 20 behavioral tests

**Total API Endpoints:** 63 fully implemented and tested endpoints ✨ UPDATED

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
query = select(Personnel).where(Personnel.nominal_roll_id == deployment.nominal_roll_id)

# Apply filters to effective assignments (overrides take precedence)
for personnel in personnel_list:
    effective_unit = override.unit if override else personnel.unit
    if filter_unit and effective_unit != filter_unit:
        continue  # Skip personnel not matching filter
```

**4. Search Functionality:**
```python
# Full-text search across name and short_id
if search_term:
    query = query.where(
        or_(
            Personnel.full_name.ilike(f"%{search_term}%"),
            Personnel.short_id.ilike(f"%{search_term}%")
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
- Edge cases (invalid deployment_id, different nominal roll, etc.)
- Role-based access control testing

**Files Modified/Created:**
- `src/parade_state/api/personnel.py` - Main API implementation
- `src/parade_state/models/schemas.py` - Added PersonnelResponseWithDeployment
- `tests/test_personnel_api.py` - Comprehensive test suite
- `src/parade_state/main.py` - Integrated personnel router

**Key Features:**
- **Deployment-Scoped Querying:** All personnel operations within deployment context
- **Override Awareness:** Shows deployment-specific unit assignments (not base nominal roll)
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
- Add attendance summary statistics (present-like/absent-like bucket counts)
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
WHERE p.nominal_roll_id = (SELECT nominal_roll_id FROM deployments WHERE id = :deployment_id)
  AND (p.unit = :filter_unit OR :filter_unit IS NULL)
  AND (p.full_name LIKE :search OR p.short_id LIKE :search OR :search IS NULL)

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
│   ├── main.py                  # FastAPI app setup and router registration
│   ├── config.py                # Configuration management
│   ├── admin_routes.py          # Admin section Jinja2 routes (/admin/*)
│   ├── api/                     # REST API endpoints (JSON)
│   │   ├── __init__.py
│   │   ├── access_control.py    # Deployment access grants + subunit scopes
│   │   ├── attendance.py        # Attendance record CRUD + bulk ops
│   │   ├── audit.py             # Audit log query
│   │   ├── auth.py              # Google OAuth flow, login/logout
│   │   ├── csv_upload.py        # CSV upload pipeline
│   │   ├── deferments.py        # Deferment CRUD (super_admin only)
│   │   ├── deployments.py       # Deployment lifecycle
│   │   ├── nominal_rolls.py            # Nominal Roll list/get/update (status, notes, label)/delete
│   │   ├── personnel.py         # Personnel listing + attendance history
│   │   ├── sessions.py          # Session open/close/reopen/finalize
│   │   └── users.py             # User CRUD + role/status transitions
│   ├── auth/                    # Auth dependencies and OAuth helpers
│   │   ├── admin_dependencies.py
│   │   ├── dependencies.py
│   │   ├── oauth.py
│   │   └── session.py
│   ├── db/                      # Database setup, Base class, session management
│   │   └── __init__.py
│   ├── migrations/              # Alembic migrations
│   │   ├── env.py
│   │   └── versions/
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── access.py            # User, AccessLevel, UserSubunitScope, DeploymentUserAccess
│   │   ├── attendance.py        # Session, AttendanceRecord
│   │   ├── audit.py             # AuditLog
│   │   ├── auth_session.py      # UserSession
│   │   ├── csv_ingestion.py     # Nominal Roll, CsvUpload, ColumnMapping, ColumnMetadata
│   │   ├── deferments.py        # Deferment
│   │   ├── deployment.py        # Deployment, overrides, notes, exclusions
│   │   ├── personnel.py         # Personnel (with callup_status)
│   │   └── schemas.py           # Pydantic request/response schemas
│   ├── utils/                   # Shared utilities (see CODE_STYLE.md)
│   │   ├── __init__.py
│   │   ├── cookies.py
│   │   ├── env.py
│   │   ├── ids.py
│   │   └── utc_dt.py
│   └── web/                     # User-facing web routes (Jinja2)
│       ├── attendance.py        # /attendance marking view
│       ├── auth.py              # /auth login/logout redirects
│       ├── deployment.py        # /deployment summary view
│       └── nominal roll.py             # /nominal-roll roster browser
├── tests/
│   ├── conftest.py              # Pytest fixtures (db, client, sample data)
│   ├── test_utils.py
│   ├── behavioral/              # Behavioral contract tests
│   ├── integration/             # API integration tests (primary suite)
│   └── unit/                    # Pure-function unit tests (ids, utc_dt)
├── docs/                        # Architecture, spec, security, deployment, etc.
├── alembic.ini
├── pyproject.toml
└── uv.lock
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

**🚨 Code Style Requirements:**
- **Read [CODE_STYLE.md](CODE_STYLE.md) before writing code**
- Utility module encapsulation is **strictly enforced**
- No direct built-in module imports (datetime, os, uuid, etc.)
- All datetime operations via `utils.utc_dt`
- All environment variables via `utils.env`
- All ID generation via `utils.ids`

**Run before commits:**

```bash
# Check code style and potential issues
uv run ruff check src/ tests/

# Format code automatically
uv run ruff format src/ tests/

# Check for type issues (when ruff type checking is fully enabled)
uv run ruff check --select TYP src/
```

**Common Violations to Avoid:**

```python
# ❌ VIOLATIONS - Direct built-in imports
import datetime
import os
import uuid
from datetime import datetime, date

# ✅ CORRECT - Use utility modules
from parade_state.utils import utc_dt, env, ids

# For type annotations
def schedule_session(date: utc_dt.date) -> utc_dt.datetime:
    return utc_dt.utcnow()
```

**CI/CD Integration:**

```yaml
# Example GitHub Actions workflow
- name: Run ruff
  run: |
    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/
```

### 5.4 Dependency Management

**Current Status:**
- **15 core dependencies** - all actively used, no bloat
- **Modern versions**: FastAPI 0.136+, Pydantic 2.13+, SQLAlchemy 2.0+
- **No security vulnerabilities** detected in current versions
- **Appropriate version constraints** (>=) allows security updates

**Dependency Categories:**
- **Core Framework**: FastAPI, Uvicorn, Pydantic, SQLAlchemy
- **Database**: asyncpg (PostgreSQL), aiosqlite (testing), Alembic (migrations)
- **Authentication**: authlib, python-multipart
- **UI/Scheduling**: nicegui, apscheduler
- **Testing**: pytest, pytest-asyncio, pytest-cov, faker

**Future Maintenance:**
1. **Security Automation**: Consider adding `pip-audit` to CI/CD for automated vulnerability scanning
2. **Version Management**: Current '>=' constraints are good for development; consider pinning major versions for production stability
3. **Regular Audits**: Quarterly dependency review recommended
4. **Update Policy**: Keep dependencies current, test upgrades before deployment

**Dependency Health Check:**
```bash
# Check for security vulnerabilities (future)
pip-audit

# Check for outdated packages
pip list --outdated

# Update dependencies safely
uv sync --upgrade
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
