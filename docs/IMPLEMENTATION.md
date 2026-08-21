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
- `tests/integration/test_api.py` - Authentication, user management, role management (18 tests)
- `tests/integration/test_attendance_api.py` - Attendance management, snapshots, constraints, CSV export scoping
- `tests/integration/test_csv_upload_api.py` - CSV upload pipeline, hash dedup, mapping (9 tests)
- `tests/integration/test_deferments_api.py` - Deferment CRUD, callup_status transitions, super_admin auth (15 tests)
- `tests/integration/test_feature_flags.py` - Flag-off hides Deferments/Grouping entirely (nav, pages, API) for every role incl. super-admin; flag-on restore; env-var defaults (8 tests)
- `tests/integration/test_environment_banner.py` - ENVIRONMENT_BANNER renders the top strip pre-auth (login) and post-auth, escapes its text, and emits no markup when unset (5 tests)
- `tests/integration/test_groupings_api.py` - Groupings (issue 26 redesign): CRUD, group-enum set replacement, memberships, member state, clone, copy-from-previous-NR, CSV export, super-admin-only mutations, flag gating
- `tests/integration/test_nominal_rolls_api.py` - Nominal Roll lifecycle (attendance activation auto-switch/deactivate, delete, label updates, CSV export)
- `tests/integration/test_personnel_api.py` - Personnel management, search, filtering (12 tests)
- `tests/integration/test_personnel_attendance_history.py` - Personnel attendance history and statistics (NR/Tagging-scoped, AM/PM slots)
- `tests/integration/test_sessions_410.py` - Sessions endpoints return 410 Gone (sessions removed in issue #4)
- `tests/integration/test_users_api.py` - User CRUD, role/status transitions (3 tests)
- `tests/integration/test_audit_api.py` - Audit log filtering and pagination (10 tests)
- `tests/integration/test_core_feature_kill_switches.py` - FEATURE_NOMINALROLL/FEATURE_ATTENDANCE default-on kill switches: unset = fully available; explicit false hides page+API+nav for every role incl. super-admin; independent gating (9 tests)

**Total:** 516 collected (512 passing, 4 skipped) ✅ UPDATED
**Coverage:** Comprehensive integration test coverage across all major features
**Performance:** ~23 seconds for full integration test suite

### 2.3 Writing New Tests

**Pattern for isolated tests:**

```python
@pytest.mark.asyncio
async def test_your_feature(db_session, sample_grouping, sample_users):
    """Test description."""
    # Arrange: Set up test data using fixtures
    user = sample_users["admin"]
    grouping = sample_grouping
    
    # Act: Perform the operation being tested
    result = await your_function(grouping.id, user.id)
    
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
sample_grouping       # Creates: sample grouping (with groups) on the sample NR
sample_sessions         # Creates: multiple session records
sample_attendance_records  # Creates: attendance records
```

**Using fixtures:**

```python
async def test_example(client, sample_users, sample_grouping):
    # Fixtures automatically provide fresh, isolated data
    admin = sample_users["admin"]
    grouping = sample_grouping

    # HTTP endpoint testing
    response = client.get(f"/api/v1/groupings/{grouping.id}")
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
- User pre-provisioning: super-admins can create accounts by email (Add User form on /admin/users) before first sign-in; promotion to super_admin/admin auto-activates unrecognised accounts
- **Endpoints:** 7 authentication + 6 user management = 13 total

**Grouping Management (✅ Redesigned in issue 26 — feature-flagged, default off)**
- A grouping is a labelled, closed vocabulary of groups based on the
  nominal roll active for attendance; servicemen hold memberships in the
  groups plus a per-grouping checkbox and free-text remarks
- Full replacement of the old design: no modes, no status lifecycle, no
  validity windows, no scheduled activation, no overrides, exclusions,
  notes, or per-grouping access scoping (those tables and endpoints were
  dropped in migration `s9f0a1b2c3d4`)
- `multiple_membership` / `allow_ungrouped` flags, immutable after creation;
  single-membership and no-ungrouped rules enforced with 400s
- Clone (same roll, optional memberships + member state) and
  copy-from-previous-NR (memberships re-linked by `pers_no`; member state
  not copied)
- Slim CSV export (Group, Rank, Name, Unit, Sub Unit, Checkbox, Remarks);
  groupings never read or write attendance
- **Feature flag:** hidden entirely (nav, `/grouping` page,
  `/api/v1/groupings/*`) unless `FEATURE_GROUPING=true` — 404 for all
  roles including super-admins; default-off posture unchanged
- Mutations super-admin only (403 otherwise); reads open to every
  authenticated role; groupings on non-active rolls unreachable (404)
- **Endpoints:** 9 grouping endpoints (CRUD, membership set, member
  state, clone, copy-from-previous, export)

**Attendance Session Management (🗑 Removed in issue #4)**
- The user-managed `Session` model (open/closed/finalized) has been removed.
- AM and PM are now hardcoded slots on a single `Attendance` row per person/day.
- `/api/v1/sessions/*` routes return 410 Gone as signposts.
- Historical reporting views that depended on sessions are broken (see issue #4
  "Out of scope") and need separate consideration.

**Attendance Management (✅ Active-NR model)**
- Attendance is taken against the one Nominal Roll currently **active for
  attendance** (`NominalRoll.attendance_active`), with its 1:1 tagging
  applied: one `Attendance` row per `(personnel, date)` carrying
  `status_am`/`remarks_am` and `status_pm`/`remarks_pm`.
- The per-NR `AttendanceScope` table and the NR confirm/unconfirm workflow
  are **removed** (migration `n4c5d6e7f8a9`): super-admins toggle
  "Use for Attendance" / "Deactivate Attendance" on the admin Nominal Rolls
  page (`POST /api/v1/nominal-rolls/{id}/activate-attendance` /
  `deactivate-attendance`); activating another NR auto-switches. With no
  active NR the user view shows an inactive message and writes are refused.
- Bulk upsert endpoint (`PUT /api/v1/attendance/upsert`) with snapshot capture;
  the same endpoint serves the per-row autosave payloads (single-record PUT).
- "Copy Remarks" endpoint (`POST /api/v1/attendance/copy-remarks`, issue 20):
  explicit source (date + slot) and destination (date + slot) — same
  source/destination is rejected (400); an optional `sub_unit_1` param narrows
  the copy to the attendance page's view filter (effective-value aware).
  Blank source remarks are skipped; missing destination rows are created.
- CSV export (`GET /api/v1/attendance/export`, issue 27): streams the marking
  table for an NR + date — statuses as display labels, personnel without a
  row export as Absent (the page's default). Honours the page's `sub_unit_1`
  filter and the Subunit-1 read-scoping rule (super_admin all; deny-by-default
  403 otherwise), so an export never leaks outside the caller's view.
- Tagging delete guarded (409) when its NR has attendance rows.
- Attendance status enum: present, absent, time_off, mc, yet_to_inpro, outpro,
  reporting_sick, late, att_out (default: absent).

**Subunit-1 Attendance Access (✅ Reworked in issue #4 PR 2)**
- New `UserSubunitAssignment(user_id, nominal_roll_id, sub_unit_1)` model —
  grants a user attendance-update rights for one sub_unit_1 on one NR.
- Server-enforced 403 on `PUT /api/v1/attendance/upsert` and
  `POST /api/v1/attendance/copy-remarks` when the caller lacks an assignment
  for a target personnel's effective sub_unit_1. Effective sub_unit_1 follows
  the NR's tagging overlay's `to_sub_unit_1` (tagging-aware), falling back
  to the personnel's canonical `sub_unit_1`.
- `super_admin` bypasses entirely; **deny-by-default** (no assignments = 403).
- Super-admin CRUD API:
  `POST /api/v1/access-control/nominal-rolls/{nr_id}/users/{user_id}/subunit-assignments`,
  `DELETE .../subunit-assignments/{assignment_id}`,
  `GET .../nominal-rolls/{nr_id}/subunit-assignments`,
  `GET .../users/{user_id}/subunit-assignments`.
- Migration `k1f2a3b4c5d6`. 332 tests passing.

**Attendance UI (✅ Active-NR model)**
- The separate super-admin `/admin/attendance` page is **removed** — it
  duplicated `/attendance`. All marking happens on `/attendance`: NR + date +
  effective sub-unit-1 filters, roster editor with AM/PM status + remarks.
- User-facing `/attendance`: defaults to the active NR; roster is filtered to
  the caller's assigned subunits (tagging-aware effective sub_unit_1;
  super_admin sees all) and shows the tagging overlay (yellow rows). With no
  active NR it shows an inactive message instead of the marking table.
- **Copy Remarks** lives on `/attendance` behind a modal (issue 20): explicit
  source/destination day + AM/PM pickers (clamped to the NR's CAA → the
  viewed day; prefilled with the old time-of-day pair), same source and
  destination blocked, an earlier destination warns and needs a second
  click, and the confirmation names the scope ("for N personnel in current
  view. Existing destination remarks will be overwritten."). Open to all
  admins — write perms are enforced server-side (sub-unit assignments, 403).
- **Autosave (issue 19):** no Save button — each row PUTs itself on status
  change or remarks blur (a "Saving…/Saved" indicator near the table; a
  failed save red-edges the row and retries on the next edit). Tagged rows
  are no longer highlighted here; yellow stays an NR-view-only signal.
- **Export CSV (issue 27):** link in the table header (beside the AM/PM
  counts) streams the displayed table for the selected NR + date +
  sub-unit filter — same contract as the Grouping page's export.
- Nominal Roll management lives on `/nominal-roll` in the collapsed-by-default
  "Roll management" expander directly below the roll selector dropdown inside
  the selector card (the Grouping page's pattern; issue 22 — it acts on the
  selected roll, so it sits next to the selector; merged from the retired
  admin Nominal Rolls page): inline label/remarks editing
  for all admins; "Use for Attendance" (auto-switch) / "Deactivate Attendance"
  / Delete for super-admins, with the same confirm dialogs as before. The
  admin page's metadata columns (source file, uploaded at, CSV hash) were
  dropped — upload provenance stays on the Upload NR page's Recent Uploads.
- **Export CSV (issue 27):** link on the roll-selector row (the Grouping
  page's placement) streams the filtered roster table — tagging overlay
  applied, the view's search/unit/sub-unit/category/rank filters honoured,
  and no 1000-row cap (`GET /api/v1/nominal-rolls/{id}/export`).

**Unit Strength Report (✅ Complete — feature-flagged, issue #25)**
- `/admin` now serves the **Unit Strength** report and the old admin
  dashboard (stat cards + recent audit activity) is removed; the post-login
  redirect to `/admin` is unchanged.
- Aggregates the attendance-active NR's Called Up personnel by effective
  (tagging-aware) sub_unit_1/sub_unit_2 into the strength reporting format:
  Officer/WOSE/Total column groups of In/Out/Current/% (In = Called Up,
  Current = present/late for the selected slot, Out = everything else
  including unmarked-as-absent, % = Current ÷ In), with SUBTOTAL per
  sub_unit_1 (shown once per section), a unit TOTAL, and a `(none)` bucket
  for personnel without subunits. `unit` and `sub_unit_3` are ignored.
- Date picker + AM/PM slot selector (URL params; server defaults today/AM,
  re-defaulted from the browser's local datetime on first visit).
- Super-admins see the whole unit; regular admins see only their assigned
  sub_unit_1 sections (same deny-by-default UserSubunitAssignment machinery
  as attendance marking) with TOTAL summing visible rows.
- **Feature flag:** hidden entirely (nav entry, `/admin` page — 404 for all
  roles including super-admins) unless `FEATURE_STRENGTH=true`.

**Sidebar Restructure (✅ workflow pages + Admin section)**
- The sidebar lists the workflow pages flat in order — Unit Strength (at
  `/admin`, flag-gated; formerly the Dashboard), Upload NR
  (relabelled from "CSV Upload"; route unchanged), Nominal Roll, Taggings,
  Deferments, Attendance, Grouping — followed by an **Admin** section:
  Users, Settings, Audit Log, Restore Backup (relabelled from "DB
  Restore"). All entries are visible to every signed-in admin; role-based
  section visibility is deferred until distinct roles exist.
- Super-admin-only pages (Taggings, Deferments, Restore Backup) are listed
  for plain admins too, but render an in-page no-access message (403, page
  shell intact) instead of silently redirecting to /admin.
- The admin Nominal Rolls and Groupings pages were retired — their
  management controls moved into the user-facing views. The issue 26
  groupings redesign later replaced the `/grouping` management expander
  with the redesigned Grouping page and deleted `/grouping/{id}/personnel`
  (see the Grouping Management notes above). The orphaned
  `/admin/sessions` redirect route was removed.

**Remap Editing (✅ comboboxes, ✅ staged edits)**
- Public NR browser: super-admins click a unit / sub-unit cell to remap it —
  the cell becomes an input with a custom suggestion panel anchored under
  the cell (the native datalist popup was replaced because its placement is
  browser-controlled); pick an existing value or type a new one, Enter
  **stages** the edit (darker-yellow pending cell; no API call). Sub-unit
  2/3 panels offer a "leave blank" pick that clears the value. Regular
  users see the read-only table.
- Staged edits are held per roll in `localStorage` (`ps:nr-edits:{roll_id}`,
  refresh-safe) until the floating bottom bar's **Apply** sends one
  `PATCH /api/v1/personnel/{id}` per person (recorded on the tagging
  overlay; row turns amber-100 on reload) or **Discard** reverts. See
  SPECIFICATION §3.4.5.
- Taggings edit modal: the cascading to-unit/to-sub selects are replaced by
  datalist inputs — remap targets may be values that don't exist on
  the NR yet (e.g. standing up a new subunit).

**Personnel Management (✅ Session 1 Complete; grouping scoping removed in issue 26)**
- Personnel listing with filtering (NR-scoped)
- Personnel detail view
- Unit hierarchy filtering (unit, sub_unit_1, sub_unit_2, sub_unit_3)
- Search functionality (name and service number)
- Personnel update operations (admin only)
- Role-based access control (admin/super_admin/user)
- The old grouping-scoped query surface (`grouping_id` params, grouping
  overrides/context in responses, grouping access checks) was removed with
  the issue 26 groupings redesign
- **Endpoints:** 3 personnel management endpoints
- **Tests:** 12+ behavioral tests

**Deferments (✅ Super-admin MVP — feature-flagged)**
- Personnel deferment CRUD linked to a single nominal roll personnel record
- `rank_name` and `sub_unit` snapshotted at creation from the linked personnel
- Reason enum (12 values) and status enum (8 values)
- Personnel `callup_status` field (`Called Up` / `Deferred` / `Disrupted` /
  `MR` / `Age Limit` / `Other`; the original three-value enum was widened and
  per-person `remarks` added — issue 06):
  - Approved deferment → `Deferred`
  - Reverting from Approved to a non-neutral status → `Called Up`
  - `Not called up` / `Do not call up` deferment statuses are neutral (no callup change)
  - Deleting an Approved deferment reverts to `Called Up`
- Super-admin-only: API and admin UI enforce `role == "super_admin"`
- Admin UI under `/admin/deferments` (nav link gated by super_admin role)
- **Feature flag:** hidden entirely (nav, page, `/api/v1/deferments/*`) unless `FEATURE_DEFERMENTS=true` — 404 for all roles including super-admins
- **Endpoints:** 5 deferment endpoints under `/api/v1/deferments`
- **Tests:** 15 behavioral tests + flag gating (test_feature_flags.py)

**Callup status & remarks columns (✅ issue 06)**
- `callup_status` widened to six values (`Called Up` default, `Deferred`,
  `Disrupted`, `MR`, `Age Limit`, `Other`); legacy `Not Called Up` rows
  migrated to `Other` (migration `q7d8e9f0a1b2`).
- New per-person `Personnel.remarks` text column (distinct from roll-level
  `NominalRoll.remarks`).
- CSV ingest maps `Callup Decision` → `callup_status` (case-insensitive
  exact match; blank → `Called Up`; unrecognised → `Other`, raw kept in
  `extra_fields`) and joins `Reason` + first `Remarks` → `remarks`.
- Attendance roster/view filters to `callup_status = 'Called Up'`; hiding is
  non-destructive — existing attendance records are never deleted or altered
  and hidden rows render with no special treatment.
- `PATCH /api/v1/personnel/{id}` accepts `callup_status` (422 on invalid) and
  `remarks` (empty/null clears); admin + super_admin.
- NR browser table shows Callup + Remarks columns with inline editing
  (select / text input, immediate PATCH) for admins and above.
- **Tests:** personnel PATCH (parametrised enum + 403), CSV mapping,
  attendance hiding + record preservation, NR view wiring

**Add Serviceman: manual creation (✅ issue 26)**
- New nullable `Personnel.source` provenance column (NULL = CSV row,
  `'manual'` = UI-added); migration `r8e9f0a1b2c3` (add_column only, chains
  on `q7d8e9f0a1b2`), exposed in Personnel responses.
- `POST /api/v1/personnel` (super-admin only; 403 otherwise): creates a row
  on an existing NR with `source='manual'`, `status='active'`,
  `callup_status` default `Called Up`, category inferred via
  `ranks.category_for_rank` (invalid rank → 400 listing valid ranks;
  unknown NR → 404; duplicate pers_no within the roll → 409 with
  IntegrityError fallback; same pers_no on a different roll allowed).
  Increments `NominalRoll.personnel_count` and writes an AuditLog
  (`personnel` / `create`) entry. pers_no may be NULL — multiple
  unknown-pers_no rows per roll are legal (unique constraint treats NULLs
  as distinct).
- `PATCH /api/v1/personnel/{id}` gains `pers_no` (fill-in-later):
  super-admin only (403 otherwise), membership semantics like `remarks`
  (explicit null / blank clears), per-roll uniqueness pre-check excluding
  self → 409. Admins retain status/callup/remarks.
- NR browser: "Add Serviceman" button below the personnel table (a roster
  action — kept out of Roll management, which acts on the roll entity;
  shown even when filters match nothing, since that's the add flow) opens a
  modal (backdrop, Esc, inline status errors,
  reload on success). Rank is a select with Officer/WOSE/Military Expert
  optgroups (closed set — the native datalist popup mispositions and
  mismatched the Callup Status select); open-vocab unit/sub-units keep
  datalist suggestions; "manual" badge beside the full name for
  `source='manual'` rows; inline-editable pers_no cell (onchange → PATCH,
  blank clears, revert on error) for super-admins, static text for others.
- Manual adds are per-roll: the next CSV upload's new roll will not include
  them (propagation out of scope).
- **Tests:** POST happy paths (with/without pers_no), permission gates,
  404/400/409, cross-roll pers_no, PATCH pers_no set/clear/duplicate/
  permissions, response `source`, NR + attendance view wiring

**Taggings (✅ 1:1 with Nominal Roll — model simplification)**
- Tagging overlay: **exactly one Tagging per Nominal Roll** (DB unique
  constraint on `nominal_roll_id`, mirroring `AttendanceScope`). Auto-created
  (empty) on NR ingestion; all unit/subunit edits land on the Tagging as
  `TaggingEntry` rows — the NR itself is read-only.
- Two entities: `Tagging` (optional informational label, NR FK CASCADE,
  audit fields) and `TaggingEntry` (one remap per person per tagging;
  4-string `from_*` / `to_*` subunit tuple).
- `from_*` auto-snapshotted from the linked personnel when omitted at
  create/edit time.
- `PATCH /api/v1/personnel/{id}` redirects unit/subunit edits to a
  TaggingEntry upsert (merged with existing entry values); identity fields
  (rank/name) are rejected with 409; `status` still mutates the personnel
  row; the response returns effective (`to_*`-overlaid) values.
- Merge-into-target: `POST /api/v1/taggings/{id}/clone` merges the source's
  entries into the target NR's existing tagging by `Personnel.pers_no`;
  already-present personnel are skipped (no clobber); unmatched source
  personnel are surfaced in the response.
- `POST /api/v1/csv/{upload_id}/process` turns a stored CSV upload into a
  full NR pipeline (NR + Personnel + ColumnMetadata + auto-tagging), with an
  optional "import taggings from another NR" source.
- The public NR browser (`/nominal-roll`) overlays effective unit/subunit
  values with a yellow row background (`.changed-row`) for tagged personnel.
- Personnel must belong to the parent tagging's NR (400 on cross-NR
  contamination). Super-admin-only: API and admin UI enforce
  `role == "super_admin"`.
- Admin UI under `/admin/taggings` (nav link gated by super_admin role):
  NR dropdown → entries-only view (from→to) with edit (per-person remap
  picker) and import-from-NR modals.
- **Endpoints:** 6 tagging endpoints under `/api/v1/taggings` + 1 CSV
  process endpoint under `/api/v1/csv/{id}/process`
- **Tests:** tagging (24) + personnel remap/409 + CSV process (7)

**Total API Endpoints:** 64 fully implemented and tested endpoints ✨ UPDATED

### 3.2 Personnel API (historical note)

The Personnel API was originally built grouping-scoped (grouping_id
params, per-grouping personnel overrides, grouping access checks). The
issue 26 groupings redesign removed that surface wholesale: personnel
endpoints take no grouping parameters, responses carry no grouping
fields, and access is nominal-roll-scoped via UserSubunitAssignment.
The attendance-history endpoint (added later) is NR/Tagging-scoped with
AM/PM slots.

```python
# ✅ Current personnel endpoints (no grouping parameters)
GET /api/v1/personnel?unit=Alpha&sub_unit_1=1stPlatoon&search=John
GET /api/v1/personnel/{id}
PATCH /api/v1/personnel/{id}
GET /api/v1/personnel/{id}/attendance-history?date_from=xxx&date_to=xxx
```

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
grouping_id: Mapped[str] = mapped_column(
    String(36), 
    ForeignKey("groupings.id", ondelete="CASCADE")
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
│   ├── features.py              # Feature-flag gate (require_feature dependency)
│   ├── admin_routes.py          # Admin section Jinja2 routes (/admin/*)
│   ├── api/                     # REST API endpoints (JSON)
│   │   ├── __init__.py
│   │   ├── access_control.py    # NR-scoped subunit assignments
│   │   ├── attendance.py        # Attendance record CRUD + bulk ops
│   │   ├── audit.py             # Audit log query
│   │   ├── auth.py              # Google OAuth flow, login/logout
│   │   ├── csv_upload.py        # CSV upload pipeline
│   │   ├── deferments.py        # Deferment CRUD (super_admin only)
│   │   ├── groupings.py      # Groupings (issue 26 redesign)
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
│   │   ├── access.py            # User, AccessLevel, UserSubunitAssignment
│   │   ├── attendance.py        # Session, AttendanceRecord
│   │   ├── audit.py             # AuditLog
│   │   ├── auth_session.py      # UserSession
│   │   ├── csv_ingestion.py     # Nominal Roll, CsvUpload, ColumnMapping, ColumnMetadata
│   │   ├── deferments.py        # Deferment
│   │   ├── grouping.py        # Grouping, GroupingGroup, GroupingMembership, GroupingMemberState
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
│       ├── grouping.py        # /grouping browser view
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
ALLOWED_ORIGINS        # Explicit CORS origins ("*" rejected in production)
APP_BASE_URL           # https://{your-app}.railway.app
```

Production is detected via `ENVIRONMENT=production` or automatically on
Railway. The app then refuses to boot without the required variables
above (no fallback secrets), sets the Secure flag on auth cookies, and
disables `/docs` / `/redoc` / `/openapi.json`.

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
