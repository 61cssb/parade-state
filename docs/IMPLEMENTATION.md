# Implementation Guide

**Version:** 1.1  
**Date:** 2026-10-07  
**Status:** Technical Implementation Guide  

---

## Table of Contents

1. [Development Setup](#1-development-setup)
2. [Testing Strategy](#2-testing-strategy)
3. [API Implementation Status](#3-api-implementation-status)
4. [Database Implementation](#4-database-implementation)
5. [Code Organization](#5-code-organization)
6. [Build & Deployment](#6-build--deployment)
7. [Performance Considerations](#7-performance-considerations)
8. [Troubleshooting](#8-troubleshooting)

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
uv run pytest tests/behavioral/test_access_control.py

# Run tests matching pattern
uv run pytest -k "test_access_level"

# Start development server
uv run uvicorn src.parade_state.main:app --reload

# Run static analysis
uv run ruff check src/
uv run ruff format src/
```

### 1.4 Pre-commit Configuration

The project uses [pre-commit](https://pre-commit.com/) with ruff hooks
(`ruff check --fix` + `ruff-format`, pinned to the ruff version in
`uv.lock` — see `.pre-commit-config.yaml`):

```bash
uv run pre-commit install   # one-time setup
uv run pre-commit run --all-files   # manual run
```

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

*Unit (`tests/unit/`, pure functions):*
- `test_config.py`, `test_db_url.py`, `test_ids.py`, `test_ranks.py`, `test_utc_dt.py`
- `tests/test_utils.py` (top-level utils tests)

*Behavioral (`tests/behavioral/`, domain contracts):*
- `test_access_control.py` - Access hierarchy and scoping rules
- `test_auth.py` - Authentication/session behavior
- `test_csv_personnel.py` - CSV → personnel domain rules

*Integration (`tests/integration/`, API + database via TestClient):*
- `test_api.py` - Authentication, user management, role management (18 tests)
- `test_admin_only_auth.py` - Admin-only gating across endpoints
- `test_admin_purge_api.py` - Super-admin purge of nominal rolls (PURGE_ENABLED)
- `test_admin_scoped_access.py` - Subunit scope grants: deny-by-default, overlay boundaries, grant CRUD
- `test_attendance_api.py` - Attendance management, snapshots, constraints, CSV export scoping
- `test_attendance_views.py` - `/attendance` page rendering and filters
- `test_audit_api.py` - Audit log filtering and pagination (10 tests)
- `test_core_feature_kill_switches.py` - FEATURE_NOMINALROLL/FEATURE_ATTENDANCE default-on kill switches: unset = fully available; explicit false hides page+API+nav for every role incl. super-admin; independent gating (9 tests)
- `test_csv_upload_api.py` - CSV upload pipeline, hash dedup, mapping (9 tests)
- `test_csv_process_api.py` - CSV → NR processing under contract v2 (issue 34): required-column errors, strict Yes-only filter + skip counts, storage map (Pers/Age optional, ORNS alias, first Remarks, Reason non-storage, extras ignored, quoted commas, old-format convergence), canonical-fixture acceptance (397/163), tagging import
- `test_db_restore_api.py` - Super-admin DB restore endpoint (validation, version guard)
- `test_deferments_api.py` - Deferment CRUD, inpro_status transitions (issue 32), super_admin auth
- `test_discussions.py` - Discussions posts/comments CRUD, markdown, triage
- `test_environment_banner.py` - ENVIRONMENT_BANNER renders the top strip pre-auth (login) and post-auth, escapes its text, and emits no markup when unset (5 tests)
- `test_feature_access.py` - Feature-access matrix (issue 37): upsert API, middleware, gates
- `test_feature_flags.py` - Flag-off hides Deferments/Grouping entirely (nav, pages, API) for every role incl. super-admin; flag-on restore; env-var defaults (8 tests)
- `test_groupings_api.py` - Groupings (issue 26 redesign): CRUD, group-enum set replacement, memberships, member state, clone, copy-from-previous-NR, CSV export, super-admin-only mutations, flag gating
- `test_migration_attendance_single_session.py` - Runs the real Alembic chain for the issue-33 attendance collapse
- `test_migration_inpro_status.py` - Runs the real Alembic chain for the callup → inpro status remap
- `test_no_client_identity.py` - Endpoints reject identity supplied via query/body (session-derived identity only)
- `test_nominal_rolls_api.py` - Nominal Roll lifecycle (attendance activation auto-switch/deactivate, delete, label updates, CSV export)
- `test_nominal_roll_views.py` - `/nominal-roll` browser rendering, remap editor, Add Serviceman
- `test_personnel_api.py` - Personnel management, search, filtering (12 tests)
- `test_personnel_attendance_history.py` - Personnel attendance history and statistics (NR/Tagging-scoped, single session)
- `test_production_hardening.py` - Production validation: refuses to boot on missing secrets, wildcard CORS; docs disabled
- `test_sessions_410.py` - Sessions endpoints return 410 Gone (sessions removed in issue #4)
- `test_sidebar_restructure.py` - Sidebar workflow pages + Admin section rendering
- `test_strength_views.py` - Unit Strength report (issue 25 + basis toggle, issue 36)
- `test_subunit_assignments_api.py` - Super-admin grant API under `/api/v1/access-control`
- `test_tagging_api.py` - Tagging CRUD, 1:1 per NR, clone/merge, personnel remap redirect
- `test_users_api.py` - User CRUD, role/status transitions (3 tests)

**Total:** 685 collected (681 passing, 4 skipped) ✅ UPDATED
**Coverage:** Comprehensive integration test coverage across all major features; 60% gate enforced in `pyproject.toml` (currently ~62%)
**Performance:** ~2.5-3 minutes for the full suite (file-based SQLite per test; set `TEST_DATABASE_URL` to a PostgreSQL server to validate the production dialect)

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
feature_flags_enabled       # Autouse: runs every test with FEATURE_* flags on
test_engine     # File-based SQLite engine per test (tmp_path/test.db),
                # or a fresh per-test PostgreSQL DB when TEST_DATABASE_URL is set
session_maker   # Creates session factory for test database
db_session      # Provides database session for direct operations
client          # Provides TestClient with database dependency override
test_db         # Alias for session_maker (backward compatibility)

# Sample data fixtures (automatically create fresh data)
sample_access_levels      # Creates: unit, coy, platoon, section
sample_users              # Creates: admin user, regular user
sample_nominal_roll       # Creates: sample nominal roll
sample_personnel          # Creates: 3 sample personnel records
sample_grouping           # Creates: sample grouping (with groups) on the sample NR
sample_grouping_memberships  # Memberships for the sample grouping
sample_attendance         # Creates: attendance rows (single session)
sample_attendance_scope   # Marks the sample NR active for attendance
admin_subunit_assignment  # Grants admins subunit scope over the sample roster

# Auth helpers
admin_token_headers        # Bearer headers for the sample admin
user_token_headers         # Bearer headers for the sample regular user
super_admin_token_headers  # Bearer headers for the well-known super admin

# tests/integration/conftest.py
well_known_users           # Stable-id users (admin-user-id, super-admin-user-id, ...)
client_as                  # TestClient acting as a given well-known user
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
- User auto-registration (unknown sign-ins land as `unrecognised`; super-admins promote or pre-provision via /admin/users)
- Role-based authorization (super_admin, admin, user)
- Session management with expiration and cleanup
- User CRUD operations with proper access control
- User pre-provisioning: super-admins can create accounts by email (Add User form on /admin/users) before first sign-in; promotion to super_admin/admin auto-activates unrecognised accounts
- **Endpoints:** 2 authentication + 5 user management = 7 total

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
- **Endpoints:** 10 grouping endpoints (create, list, get, update, delete,
  group-set replacement, member state, clone, copy-from-previous, export)

**Attendance Session Management (🗑 Removed in issue #4; slots removed in #33)**
- The user-managed `Session` model (open/closed/finalized) has been removed.
- The AM/PM split was later removed too (issue 33): one session per day on a
  single `Attendance` row per person/day.
- `/api/v1/sessions/*` routes return 410 Gone as signposts.
- Historical reporting views that depended on sessions are broken (see issue #4
  "Out of scope") and need separate consideration.

**Attendance Management (✅ Active-NR model, single session — issue 33)**
- Attendance is taken once daily against the one Nominal Roll currently
  **active for attendance** (`NominalRoll.attendance_active`), with its 1:1
  tagging applied: one `Attendance` row per `(personnel, date)` carrying
  `status` (present/absent), an optional `reason` enum, and `remarks`.
- The marking roster is **everyone** on the NR (deferred included) — the
  page's Inpro Status column + filter are view concerns (issue 33; the
  issue-32 interim `!= 'deferred'` roster filter is gone).
- The per-NR `AttendanceScope` table and the NR confirm/unconfirm workflow
  are **removed** (migration `n4c5d6e7f8a9`): super-admins toggle
  "Use for Attendance" / "Deactivate Attendance" on the admin Nominal Rolls
  page (`POST /api/v1/nominal-rolls/{id}/activate-attendance` /
  `deactivate-attendance`); activating another NR auto-switches. With no
  active NR the user view shows an inactive message and writes are refused.
- Bulk upsert endpoint (`PUT /api/v1/attendance/upsert`) with snapshot capture;
  the same endpoint serves the per-row autosave payloads (single-record PUT).
- "Copy Remarks" endpoint (`POST /api/v1/attendance/copy-remarks`, issue 20;
  single-session rework in #33): explicit source date and destination date —
  same date is rejected (400); an optional `sub_unit_1` param narrows
  the copy to the attendance page's view filter (effective-value aware).
  Blank source remarks are skipped; missing destination rows are created.
- CSV export (`GET /api/v1/attendance/export`, issue 27): streams the marking
  table for an NR + date — columns mirror the page (…, Name, Inpro Status,
  Status, Reason, Remarks), statuses/reasons as display labels, personnel
  without a row export as Absent (the page's default). Honours the page's
  `sub_unit_1` filter and the Subunit-1 read-scoping rule (super_admin all;
  deny-by-default 403 otherwise), so an export never leaks outside the
  caller's view.
- Tagging delete guarded (409) when its NR has attendance rows.
- Attendance status enum (issue 33): present, absent (default: absent).
  Reason enum (nullable, never feeds reporting): mc, off, early_outpro,
  other, awol. Migration `w4d5e6f7a8b9` collapses the legacy 9-value AM/PM
  vocabulary per the issue-33 mapping table (PM wins when its slot was
  marked; remarks joined with `"; "`; "Late" appended).
- Day freeze (issue 35): `attendance_freezes` table (migration
  `x5e6f7a8b9c0`; one row per frozen NR/day, cascades with the NR).
  `PUT /api/v1/attendance/freeze` (JSON body) / `DELETE` (query params)
  toggle it — super-admin only, active-NR gated, 409 on double-freeze /
  404 on unfreezing a non-frozen day, audit-logged under action
  `attendance_freeze` (entity `nominal_roll`). Upsert (any touched date)
  and copy-remarks (destination date) 403 for non-super-admins when a
  frozen (NR, date) is written; super-admins keep editing (retro-edit
  rules unchanged). The attendance page shows a Freeze/Unfreeze toggle
  (super-admins), a frozen banner naming the freeze timestamp (every
  role), and a plain-text read-only grid for admins on frozen days.

**Feature-Access Matrix (✅ issue 37)**
- `feature_access` table (migration `y6f7a8b9c0d1`; `(feature_key, role)`
  unique; `audit_entity_type` widened with `feature_access`) — per-role
  feature visibility, fail-open (absent row = enabled), `super_admin`
  never configurable. Effective visibility = `FEATURE_*` env flag AND
  matrix entry; env off (404, everyone) outranks matrix off (403, the
  configured role only).
- Seam module `src/parade_state/feature_access.py` —
  `FeatureAccessMiddleware` (one tiny SELECT per page request, stashed on
  `request.state.feature_access` for the sidebar; fails open),
  `feature_allowed` (page-route gate → styled 403 shell), and
  `require_feature_access(key)` (router-level API dependency; wired in
  `main.py` onto personnel + nominal-rolls → `nominal_roll`, attendance →
  `attendance`, groupings → `grouping`).
- Settings: super-admin-only page gate (matching Restore Backup) hosting
  the "Feature access (admins)" card → `POST /api/v1/admin/feature-access`
  (full-matrix upsert, value changes audit-logged under
  entity `feature_access`, action `update`). Sidebar Admin section:
  Users/Settings/Restore Backup render for super-admins only; Audit Log
  stays admin-viewable (2026-08-27 decision).

**Scope Access (✅ issue #4 PR 2; ✅ extended by issue #28)**
- `UserSubunitAssignment(user_id, nominal_roll_id, unit, sub_unit_1)` — a
  grant is a (unit, sub_unit_1) pair on one NR with the explicit `*`
  wildcard sentinel per column; `(*, *)` is forbidden by CHECK constraint;
  unique per (user, NR, unit, sub_unit_1). Migration `k1f2a3b4c5d6` (issue
  #4) + `u2b3c4d5e6f7` (issue #28 unit dimension; validated on the
  repro-pg roundtrip).
- Shared enforcement module `api/subunit_access.py` (issue #31 ✅ landed:
  callers pass the session-derived `user.id`/`user.role` from the
  `auth/dependencies.py` dependencies): `get_scope_grants`, `grant_matches`,
  `resolve_effective_locations` (tagging overlay applied verbatim, else
  canonical), `assert_nr_accessible` / `assert_locations_in_scope` (write
  403s naming the missing "unit/sub-unit"), `in_scope_pids` (non-raising
  read filter), `accessible_nr_ids`.
- Enforced surfaces (regular admins; super_admin bypasses; all
  deny-by-default; client filters only narrow):
  `GET /personnel` (single-NR 403 without grants; cross-NR restricted to
  granted NRs; pagination applied after overlay-aware filtering),
  `GET/PATCH /personnel/{id}`, `GET /personnel/{id}/attendance-history`,
  `GET /attendance/`, `PUT /attendance/upsert`,
  `POST /attendance/copy-remarks`, `GET /attendance/export`,
  `GET /nominal-rolls` (granted NRs only), `GET/PATCH /nominal-rolls/{id}`,
  `GET /nominal-rolls/{id}/export`, the strength report, and the NR
  browser + attendance pages (scoped roster, dropdown options derived
  from visible rows, no-assignments banner).
- CSV upload/process tightened to super-admin only (NR lifecycle op).
- Super-admin grant API under `/api/v1/access-control`: grant/revoke/list
  (list responses carry the NR display label) +
  `GET /nominal-rolls/{nr_id}/scope-options` (roster units and
  unit→sub-unit values for the form). Grant values validated against the
  roster (case-sensitive); `''` never means wildcard.
- UI: the Scope panel on `/admin/users` (issue 28) — per-user grants
  grouped by NR, cascading NR → unit → sub-unit grant form with
  "All units"/"All sub-units" wildcard options, revoke buttons; auto-save
  via fetch like the discussions triage pattern.
- Discussions board stays org-wide for all admins (posts carry no
  NR/personnel linkage; recorded decision for the issue 24 flag rollout).
- Tests: `tests/integration/test_admin_scoped_access.py` (vocabulary,
  deny-by-default across surfaces, overlay in/out boundaries, cosmetic
  filter non-bypass, pagination under overlay, grant CRUD validation,
  check constraint).

**Attendance UI (✅ Active-NR model, single session — issue 33)**
- The separate super-admin `/admin/attendance` page is **removed** — it
  duplicated `/attendance`. All marking happens on `/attendance`: NR + date +
  effective sub-unit-1 + Inpro Status + Status + Reason view filters, roster
  editor with status + reason + remarks.
- User-facing `/attendance`: defaults to the active NR; the roster is **all
  NR personnel** (deferred included — issue 33), filtered to the caller's
  assigned subunits (tagging-aware effective sub_unit_1; super_admin sees
  all) with a read-only Inpro Status column just before the status column
  and an Inpro Status filter (e.g. hide Deferred), plus Status / Reason
  view filters (non-destructive, same contract). With no active NR it
  shows an inactive message instead of the marking table. The viewed day
  defaults server-side to UTC today; a first-visit script re-defaults it
  to the browser's local day (best effort — mirrors the strength report;
  UTC lagged SGT mornings until 08:00).
- **Copy Remarks** lives on `/attendance` behind a modal (issue 20;
  single-session rework in #33): explicit source/destination date pickers
  (clamped to the NR's CAA → the viewed day; prefilled with the previous
  day), same source and destination blocked, an earlier destination warns
  and needs a second click, and the confirmation names the scope ("for N
  personnel in current view. Existing destination remarks will be
  overwritten."). Open to all admins — write perms are enforced server-side
  (sub-unit assignments, 403).
- **Autosave (issue 19):** no Save button — each row PUTs itself on
  status/reason change or remarks blur (a "Saving…/Saved" indicator near the
  table; a failed save red-edges the row and retries on the next edit).
  Tagged rows are no longer highlighted here; yellow stays an NR-view-only
  signal.
- **Export CSV (issue 27):** link in the table header (beside the
  present/marked counts) streams the displayed table for the selected NR +
  date + sub-unit filter — same contract as the Grouping page's export.
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
- Aggregates the attendance-active NR's non-deferred personnel by effective
  (tagging-aware) sub_unit_1/sub_unit_2 into the strength reporting format:
  Officer/WOSE/Total column groups of In/Out/Current/% (In = not deferred,
  Current = present — single daily session, issue 33; reason never
  participates, Out = everything else
  including unmarked-as-absent, % = Current ÷ In), with SUBTOTAL per
  sub_unit_1 (shown once per section), a unit TOTAL, and a `(none)` bucket
  for personnel without subunits. `unit` and `sub_unit_3` are ignored.
- Date picker (URL param; server default today, re-defaulted from the
  browser's local datetime on first visit). The AM/PM slot selector was
  removed with the single-session rework (issue 33).
- Reporting basis (✅ issue #36): `?basis=tagged|untagged` (default
  `tagged`, unknown values fall back). Tagged groups under the effective
  (tagging-applied) subunits; **untagged** groups under the original NR
  allocations — the canonical `Personnel` columns, tagging overlay not
  applied (`from_*` snapshots never consulted). Untagged is
  super-admin-only: the segmented Tagged/Untagged control (radio-based,
  active segment highlighted) renders for super-admins alone and
  any other role explicitly requesting it gets the 403 no-access page.
  The heading names the active basis so rendered output is
  self-describing; the unit-wide TOTAL is allocation-independent and
  identical on both bases.
- Super-admins see the whole unit; regular admins see only their assigned
  sub_unit_1 sections (same deny-by-default UserSubunitAssignment machinery
  as attendance marking) with TOTAL summing visible rows.
- **Feature flag:** hidden entirely (nav entry, `/admin` page — 404 for all
  roles including super-admins) unless `FEATURE_STRENGTH=true`.

**Sidebar Restructure (✅ workflow pages + Admin section)**
- The sidebar lists the workflow pages flat in order — Upload NR
  (relabelled from "CSV Upload"; route unchanged), Nominal Roll, Taggings,
  Deferments, Attendance, Unit Strength (at
  `/admin`, flag-gated; formerly the Dashboard — moved after Attendance
  2026-08-26 so reporting follows marking), Grouping — followed by an **Admin** section:
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

**Remap Editing (✅ comboboxes, ✅ staged edits, ✅ admin sub 2/3 — issue 38)**
- Public NR browser: super-admins click a unit / sub-unit cell to remap it —
  the cell becomes an input with a custom suggestion panel anchored under
  the cell (the native datalist popup was replaced because its placement is
  browser-controlled); pick an existing value or type a new one, Enter
  **stages** the edit (darker-yellow pending cell; no API call). Sub-unit
  2/3 panels offer a "leave blank" pick that clears the value. Regular
  users see the read-only table.
- Issue 38: in-scope admins get the same editor on **sub-unit 2/3 cells
  only** — unit / sub-unit 1 render read-only and their suggestion lists
  are not shipped. Enforced at the API seam, not the UI: `PATCH
  /api/v1/personnel/{id}` rejects `unit`/`sub_unit_1` for non-super-admins
  with 403 (whole payload, nothing applied, checked before the scope
  gate); sub 2/3 remaps stay scope-gated only. Closes the pre-38 hole
  where any in-scope admin could PATCH the top two levels.
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
- **Endpoints:** 5 personnel endpoints under `/api/v1/personnel`
- **Tests:** 12+ behavioral tests

**Deferments (✅ Super-admin MVP — feature-flagged)**
- Personnel deferment CRUD linked to a single nominal roll personnel record
- `rank_name` and `sub_unit` snapshotted at creation from the linked personnel
- Reason enum (12 values) and status enum (8 values)
- Personnel `inpro_status` transitions (issue 32):
  - Approving a deferment never auto-sets inpro_status — the admin UI
    prompts "set Inpro status to Deferred?" and PATCHes the personnel
    separately when confirmed (declining leaves it unchanged)
  - Moving an Approved deferment to any other status (neutral statuses
    included) → unconditional revert to `yet_to_inpro`
  - Deleting an Approved deferment reverts to `yet_to_inpro`
- Super-admin-only: API and admin UI enforce `role == "super_admin"`
- Admin UI under `/admin/deferments` (nav link gated by super_admin role)
- **Feature flag:** hidden entirely (nav, page, `/api/v1/deferments/*`) unless `FEATURE_DEFERMENTS=true` — 404 for all roles including super-admins
- **Endpoints:** 5 deferment endpoints under `/api/v1/deferments`
- **Tests:** behavioral transition tests + flag gating (test_feature_flags.py)

**Inpro status & remarks columns (✅ issue 06; reworked by issue 32)**
- `callup_status` (six-value callup-decision enum, issue 06) replaced by
  `inpro_status` — the 3-value in-processing lifecycle `inproed` /
  `yet_to_inpro` (default) / `deferred` — via migration `v3c4d5e6f7a8`
  (PostgreSQL native-enum rebuild + SQLite batch rebuild). Mapping:
  Called Up → yet_to_inpro; Deferred → deferred; Disrupted/MR/Age
  Limit/Other → yet_to_inpro with `Previously: <value>` appended to
  `remarks` (per-value remap counts logged; downgrade is lossy — inproed
  collapses to Called Up and the appended remarks stay).
- Per-person `Personnel.remarks` text column (distinct from roll-level
  `NominalRoll.remarks`).
- CSV ingest (interim shim, superseded 2026-08-25 by the issue 34
  contract v2 below): legacy `Callup Decision` remapped — Yes / blank /
  Called Up → `yet_to_inpro`; No / Deferred → `deferred`; anything else →
  `yet_to_inpro` + `Previously: <value>` remark — and joins `Reason` +
  first `Remarks` → `remarks`.
- Attendance roster/view/dashboard filter to `inpro_status != 'deferred'`
  (interim rule until #33); hiding is non-destructive — existing attendance
  records are never deleted or altered and hidden rows render with no
  special treatment.
- `PATCH /api/v1/personnel/{id}` accepts `inpro_status` (422 on invalid) and
  `remarks` (empty/null clears); **issue 39 (admin trial):** `inpro_status`
  is super-admin-only — 403 for admins alone or mixed with allowed fields,
  nothing applied, checked before the scope gate (same shape as `pers_no`
  and #38's unit/sub-unit 1); `remarks` stays admin + super_admin.
- NR browser table shows Inpro Status + Remarks columns; remarks keep
  inline editing (text input, immediate PATCH) for admins and above, the
  Inpro select is super-admin-rendered (issue 39 — admins get the plain
  label; the handler is not shipped), plus a user-side filter by Inpro
  status (e.g. hide Deferred) carried into the CSV export.
- **Tests:** personnel PATCH (parametrised enum + 422 on the retired
  vocabulary + 403), CSV shim mapping, attendance hiding + record
  preservation, NR view wiring + filter, migration mapping
  (test_migration_inpro_status.py runs the real alembic chain)

**CSV Ingestion (✅ contract v2 — issue 34)**
- Header-name matching replaces the index-based 18-column map
  (`parade_state.utils.csv_constants`: `_HEADER_SPEC` +
  `resolve_columns`); first occurrence of a name wins, extras tolerated
  and ignored. Shared by the app process endpoint and the demo ingester.
- Required columns (missing → 400 naming the column, blank Unit header
  included): Unit, Sub Unit 1-3, Rank, Full Name, Callup Decision,
  Reason, Remarks, HK ICT, ORNS (alias `ORNS Yrs`).
- Strict Yes-only row filter: only an exact case-insensitive `Yes` stores
  a row; everything else (No, blank, Y, free text) is skipped and counted
  (`decision_skipped` breakdown in the process response/report).
- Storage: core columns; optional Pers → `pers_no` (blank/absent → NULL);
  first Remarks column only → `personnel.remarks`; ORNS/ORNS Yrs →
  `extra_fields.orns`, HK ICT → `extra_fields.hk_ict`, optional Age(Yr) →
  `extra_fields.age_yr` (ints); Callup Decision and Reason are read but
  never stored. New personnel default `inpro_status = yet_to_inpro`
  (the issue-32 interim shim is removed).
- The pre-v2 16-column WY2627 export (ORNS, Age(Yr), no Pers) also
  ingests cleanly — the formats converge under name matching.
- Canonical fixture acceptance (in tests): 397 stored / 163 skipped /
  2 NULL pers_no. Demo ingester (`experiments/csv_to_nr/ingest.py`) and
  demo DB regenerated on the new contract.
- **Tests:** `tests/integration/test_csv_process_api.py` (reworked).

**Add Serviceman: manual creation (✅ issue 26)**
- New nullable `Personnel.source` provenance column (NULL = CSV row,
  `'manual'` = UI-added); migration `r8e9f0a1b2c3` (add_column only, chains
  on `q7d8e9f0a1b2`), exposed in Personnel responses.
- `POST /api/v1/personnel` (super-admin only; 403 otherwise): creates a row
  on an existing NR with `source='manual'`, `status='active'`,
  `inpro_status` default `yet_to_inpro`, category inferred via
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
  self → 409. Admins retain status/remarks (inpro left this list at
  issue 39; sub 2/3 arrived with #38).
- NR browser: "Add Serviceman" button below the personnel table (a roster
  action — kept out of Roll management, which acts on the roll entity;
  shown even when filters match nothing, since that's the add flow) opens a
  modal (backdrop, Esc, inline status errors,
  reload on success). Rank is a select with Officer/WOSE/Military Expert
  optgroups (closed set — the native datalist popup mispositions and
  mismatched the Inpro Status select); open-vocab unit/sub-units keep
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
  full NR pipeline (NR + Personnel + ColumnMetadata + auto-tagging) under
  the ingestion contract v2 (issue 34 — see the CSV Ingestion section),
  with an optional "import taggings from another NR" source.
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

**Total API Endpoints:** 68 route handlers across the 17 `api/` modules
(67 live endpoints + the `/api/v1/sessions` 410-Gone catch-all) ✨ UPDATED

### 3.2 Personnel API (historical note)

The Personnel API was originally built grouping-scoped (grouping_id
params, per-grouping personnel overrides, grouping access checks). The
issue 26 groupings redesign removed that surface wholesale: personnel
endpoints take no grouping parameters, responses carry no grouping
fields, and access is nominal-roll-scoped via UserSubunitAssignment.
The attendance-history endpoint (added later) is NR/Tagging-scoped with
single-session per-day rows and stats (issue 33).

```python
# ✅ Current personnel endpoints (no grouping parameters)
GET /api/v1/personnel?unit=Alpha&sub_unit_1=1stPlatoon&search=John
GET /api/v1/personnel/{id}
PATCH /api/v1/personnel/{id}
GET /api/v1/personnel/{id}/attendance-history?date_from=xxx&date_to=xxx
```

---

## 4. Database Implementation

> **Scope note (2026-10-08):** the `ippt_*` tables of the IPPT
> monitoring feature (FEATURE_IPPT) live outside this guide's database
> section — see [docs/IPPT_MONITORING.md](IPPT_MONITORING.md) §6,
> `src/parade_state/models/ippt.py`, and
> `src/parade_state/migrations/versions/z7g8h9i0j1k2_add_ippt_monitoring.py`
> (+ `a8b9c0d1e2f3`).

### 4.1 Database Choice Rationale

**Production: PostgreSQL**
- Native UUID support
- JSONB for flexible schema evolution
- Partial unique indexes for business rules
- ACID compliance for data integrity
- Proven reliability at scale

**Testing: SQLite (file-based, one per test)**
- Fast test execution
- Complete test isolation (each test gets its own database file under `tmp_path`)
- Async support via aiosqlite
- No external dependencies
- Cross-platform compatibility
- Optional: set `TEST_DATABASE_URL` to run the suite against PostgreSQL
  (a fresh database is created per test and dropped on teardown)

### 4.2 Schema Management

**Current Status:** Alembic migrations, async environment.

- `alembic.ini` points `script_location` at `src/parade_state/migrations`;
  `migrations/env.py` runs migrations through the async engine and applies
  `normalize_database_url` to the `DATABASE_URL` environment variable
  (so platform `postgresql://` URLs work as-is).
- The `versions/` directory holds the full revision chain (26 revisions),
  including the data-migrating steps (attendance single-session collapse,
  inpro-status remap — both covered by tests that run the real chain).
- The production container runs `alembic upgrade head` before uvicorn
  accepts traffic (see the Dockerfile CMD).
- The admin-UI restore path checks the restored database's Alembic revision
  and upgrades it to head if it is behind the code.

```bash
# Generate a migration from model changes
uv run alembic revision --autogenerate -m "Describe the change"

# Apply migrations (local dev; the container does this automatically)
uv run alembic upgrade head

# Production database migration (or let the container CMD handle it)
DATABASE_URL=postgresql://... uv run alembic upgrade head
```

### 4.3 UUID Storage Implementation

**Cross-Database UUID Strategy:**

```python
# Base class (src/parade_state/db/__init__.py)
class Base(DeclarativeBase):
    id: Mapped[str] = mapped_column(
        String(36),              # String storage for SQLite compatibility
        primary_key=True,
        default=ids.db_default,  # parade_state.utils.ids — UUID4 as string
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

**PostgreSQL note:**

IDs stay String(36) in production PostgreSQL too — the same representation
on both dialects means no native-UUID migration is needed or planned.

### 4.4 JSON vs JSONB

**Implementation:**

```python
# Personnel.extra_fields uses JSON type
extra_fields: Mapped[dict] = mapped_column(JSON, default=dict)
```

**Behavior:**
- SQLite: Stores as JSON text, automatic serialization/deserialization
- PostgreSQL: Stores as JSON (SQLAlchemy's generic JSON type — not JSONB)
- Application layer: Works with Python dicts seamlessly

**Possible future optimization (not currently needed):**

```sql
-- Migrate JSON to JSONB for better query performance
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

### 5.1 Project Structure

```
parade-state/
├── src/parade_state/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory, middleware, routers, lifespan
│   ├── config.py                # Settings from env + production validation
│   ├── features.py              # Feature-flag gate (require_feature dependency)
│   ├── feature_access.py        # Per-role feature matrix (middleware + gates, issue 37)
│   ├── admin_routes.py          # Admin section Jinja2 routes (/admin/*)
│   ├── api/                     # REST API endpoints (JSON)
│   │   ├── __init__.py
│   │   ├── access_control.py    # Super-admin subunit grant API (NR-scoped)
│   │   ├── admin_purge.py       # Super-admin purge of all NRs (testing-only flag)
│   │   ├── attendance.py        # Attendance list/upsert/copy-remarks/freeze/export
│   │   ├── audit.py             # Audit log query
│   │   ├── auth.py              # /me + /logout (OAuth lives in web/auth.py)
│   │   ├── csv_upload.py        # CSV upload pipeline (upload, list, process)
│   │   ├── db_restore.py        # Super-admin DB restore endpoint
│   │   ├── deferments.py        # Deferment CRUD (super_admin only)
│   │   ├── discussions.py       # Discussions posts/comments + triage
│   │   ├── feature_access.py    # Feature-access matrix upsert (super_admin only)
│   │   ├── groupings.py         # Groupings (issue 26 redesign)
│   │   ├── nominal_rolls.py     # NR lifecycle + attendance activation + CSV export
│   │   ├── personnel.py         # Personnel listing/add/remaps/attendance history
│   │   ├── sessions.py          # 410 Gone signposts (sessions removed)
│   │   ├── subunit_access.py    # Shared NR-scoped enforcement seam (issue #28/#31)
│   │   ├── tagging.py           # Tagging overlay CRUD + clone
│   │   └── users.py             # User CRUD + role/status transitions
│   ├── auth/                    # Auth dependencies and OAuth helpers
│   │   ├── __init__.py
│   │   ├── admin_dependencies.py
│   │   ├── dependencies.py      # Bearer-or-cookie session resolution + role deps
│   │   ├── oauth.py             # Authlib Google client (OAuth flows only)
│   │   └── session.py           # UserSession create/validate/invalidate/cleanup
│   ├── db/                      # Database setup, Base class, session management
│   │   ├── __init__.py
│   │   └── restore.py           # Verified in-app restore from pg_dump archive
│   ├── migrations/              # Alembic migrations (async env)
│   │   ├── env.py
│   │   └── versions/            # 26 revisions (full schema history)
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── access.py            # User, AccessLevel, UserSubunitAssignment, FeatureAccess
│   │   ├── attendance.py        # Attendance, AttendanceFreeze (single daily session)
│   │   ├── audit.py             # AuditLog
│   │   ├── auth_session.py      # UserSession (DB-backed tokens)
│   │   ├── csv_ingestion.py     # Nominal Roll, CsvUpload, ColumnMapping, ColumnMetadata
│   │   ├── deferments.py        # Deferment
│   │   ├── discussions.py       # DiscussionPost, DiscussionComment
│   │   ├── grouping.py          # Grouping, GroupingGroup, GroupingMembership, GroupingMemberState
│   │   ├── personnel.py         # Personnel (with inpro_status)
│   │   ├── schemas.py           # Pydantic request/response schemas
│   │   └── tagging.py           # Tagging, TaggingEntry (1:1 with NR)
│   ├── templates/               # Jinja2 pages (shared base.html shell)
│   │   ├── admin/               # Admin section templates (users, taggings, audit, ...)
│   │   ├── attendance.html      # /attendance marking view
│   │   ├── login.html, no_access.html, auth_callback.html
│   │   ├── grouping.html, nominal_roll.html
│   │   ├── feature_disabled.html
│   │   └── base.html
│   ├── utils/                   # Shared utilities (see CODE_STYLE.md)
│   │   ├── __init__.py
│   │   ├── cookies.py           # HttpOnly auth cookie set/get/clear
│   │   ├── csv_constants.py     # CSV ingestion contract v2 (header-name matching)
│   │   ├── env.py
│   │   ├── ids.py
│   │   ├── markdown.py          # Safe markdown rendering for discussions
│   │   ├── ranks.py             # Rank-to-category mapping
│   │   └── utc_dt.py
│   └── web/                     # User-facing web routes (Jinja2)
│       ├── __init__.py
│       ├── attendance.py        # /attendance marking view
│       ├── auth.py              # /auth login/logout/callback/no-access
│       ├── grouping.py          # /grouping browser view
│       └── nominal_roll.py      # /nominal-roll roster browser
├── tests/
│   ├── conftest.py              # Pytest fixtures (db, client, sample data)
│   ├── test_utils.py
│   ├── behavioral/              # Behavioral contract tests
│   ├── integration/             # API integration tests (primary suite)
│   └── unit/                    # Pure-function unit tests (config, db_url, ids, ranks, utc_dt)
├── scripts/
│   ├── alpha_sanity_check.py    # Alpha-milestone sanity checks
│   └── visual_check.py          # Playwright visual checks
├── .github/workflows/           # ci.yml, pip-audit.yml, backup-db.yml
├── docs/                        # Architecture, spec, security, deployment, etc.
├── alembic.ini
├── Dockerfile                   # Production image (migrations + uvicorn)
├── pyproject.toml
└── uv.lock
```

### 5.2 Model Organization

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

### 5.3 Database Session Management

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

### 6.1 Local Development

**Development server:**

```bash
# Run with auto-reload
uv run uvicorn src.parade_state.main:app --reload --host 0.0.0.0 --port 8000
```

**Database setup (local SQLite by default):**

The repo `.env` points `DATABASE_URL` at a local SQLite file
(`sqlite+aiosqlite:///parade_state.db`). Create the schema with Alembic
before first run:

```bash
uv run alembic upgrade head
```

**Optional: local PostgreSQL**

```bash
docker run --name parade-state-postgres \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=parade_state \
  -p 5432:5432 \
  -d postgres:18

# Set environment variables
export DATABASE_URL="postgresql://postgres:password@localhost:5432/parade_state"
uv run alembic upgrade head
```

### 6.2 Production Deployment (Railway)

**Environment variables:**

```bash
DATABASE_URL           # Injected automatically by Railway Postgres add-on
SUPER_ADMIN_EMAIL      # Super admin email for bootstrap
GOOGLE_CLIENT_ID       # Google OAuth client ID
GOOGLE_CLIENT_SECRET   # Google OAuth client secret
SESSION_SECRET         # Session encryption secret
ALLOWED_ORIGINS        # Explicit CORS origins ("*" rejected in production)
APP_BASE_URL           # https://{your-app}.railway.app

# Optional
AUTH_COOKIE_SECURE     # Secure flag on auth cookies (default: on in production)
ENVIRONMENT_BANNER     # Non-production identifier strip
FEATURE_*              # Feature flags / kill switches (see config.py)
```

Production is detected via `ENVIRONMENT=production` or automatically on
Railway (the platform injects `RAILWAY_PROJECT_ID`/`RAILWAY_SERVICE_ID`).
The app then refuses to boot without the required variables above (no
fallback secrets), sets the Secure flag on auth cookies, and disables
`/docs` / `/redoc` / `/openapi.json`.

**Railway deployment:**

1. Push to main branch → Railway builds the `Dockerfile`
   (python:3.12-slim + uv; `uv sync --frozen --no-dev`; non-root user)
2. The image installs `postgresql-client-18` (PGDG) so the admin-UI
   database restore can run `pg_restore` against the PostgreSQL 18 server
3. The container CMD runs DB migrations, then starts uvicorn

**Start command (Dockerfile CMD):**

```bash
alembic upgrade head && uvicorn parade_state.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*
```

`--proxy-headers` lets uvicorn honor Railway's `X-Forwarded-Proto` so
`request.url.scheme` is https behind the edge proxy (required for OAuth
redirect URIs and Secure cookies).

### 6.3 Static Analysis

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

# Type-check with pyright (dev dependency group)
uv run pyright
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

`.github/workflows/ci.yml` runs on every PR and push to main:

```yaml
# Lint job
- name: Ruff lint
  run: uv run ruff check src tests scripts

- name: Ruff format check
  run: uv run ruff format --check src tests scripts

# Test job
- name: Run test suite
  run: uv run pytest -q
# ... then uploads htmlcov/ as a coverage artifact
```

Two other workflows exist: `pip-audit.yml` (weekly audit of the production
dependency set) and `backup-db.yml` (daily encrypted `pg_dump` backup).

### 6.4 Dependency Management

**Current Status:**
- **15 core dependencies** - all actively used, no bloat
- **Modern versions**: FastAPI >=0.104, Pydantic >=2.5, SQLAlchemy >=2.0 (>= constraints allow security updates; the lockfile pins exact versions)
- **Dev dependency group**: pyright, ruff, pre-commit, pytest, pytest-asyncio, pytest-cov, pip-audit

**Dependency Categories:**
- **Core Framework**: FastAPI, Uvicorn, Pydantic, Starlette
- **Database**: asyncpg (PostgreSQL), aiosqlite (SQLite dev/testing), Alembic (migrations), SQLAlchemy
- **Authentication**: authlib (OAuth client), httpx (its HTTP transport)
- **Pages/Templates**: Jinja2, markdown2, itsdangerous (session cookie signing), python-dotenv
- **File uploads**: python-multipart

**Maintenance:**
1. **Security Automation**: `pip-audit` runs weekly via `.github/workflows/pip-audit.yml` (manual dispatch for immediate checks)
2. **Version Management**: Current '>=' constraints are good for development; consider pinning major versions for production stability
3. **Regular Audits**: Quarterly dependency review recommended
4. **Update Policy**: Keep dependencies current, test upgrades before deployment

**Dependency Health Check:**
```bash
# Check for security vulnerabilities
uv run pip-audit

# Check for outdated packages
pip list --outdated

# Update dependencies safely (then commit the updated uv.lock)
uv sync --upgrade
```

---

## 7. Performance Considerations

### 7.1 Database Query Optimization

**Current optimizations:**
- Indexed foreign keys for fast joins
- Indexed email for user login
- Indexed status fields for common queries
- Indexed dates for session lookups

**Future optimizations:**
- Add composite indexes for common query patterns
- Use database EXPLAIN ANALYZE to identify slow queries
- Consider read replicas for heavy read operations

### 7.2 Async Operations

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

### 8.1 Common Development Issues

**Import errors:**
- Ensure you've run `uv sync` after pulling changes (the package is
  installed into the venv from `src/`; no manual PYTHONPATH needed)

**Test failures:**
- Each test is independent - failures are self-contained
- Check that fixtures are being used correctly
- Verify database isolation by running tests individually

**Database connection issues:**
- Check DATABASE_URL is set correctly
- Verify PostgreSQL server is running
- Ensure database migrations are up to date

### 8.2 Debugging Tips

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

*End of Implementation Guide v1.1*
