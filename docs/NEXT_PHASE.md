# Next Implementation Phase

**Last Updated:** 2026-07-01
**Status:** Production-Ready Backend with User-Facing Views

---

## Current System Status

### Production-Ready Metrics
- 245 tests passing (100% pass rate)
- 31+ API endpoints fully implemented and tested
- Enterprise-grade security with multi-tenant access control
- Comprehensive documentation (architecture, security, deployment, testing)
- Database migrations initialized and production-ready

### Completed Core Features
- Google OAuth authentication & role-based access control
- **Admin interface with Jinja2 templates** (modern responsive UI)
- **Host-independent OAuth flow** (works with any domain/hostname)
- Complete deployment management (lifecycle, overrides, notes)
- Attendance session management (AM/PM sessions, status transitions)
- Comprehensive attendance tracking (individual & bulk operations)
- Personnel management API (deployment-based listing, filtering, search)
- **Advanced access control** (deployment-based multi-tenant security)
- **CSV file upload** (SHA256 hashing, duplicate detection, column parsing)
- **User management admin page** (inline role/status editing, search/filter, audit logging)
- **Audit log API + admin page** (filterable, paginated, colored action badges)
- **Combined deployment + session admin page** (master-detail with status transitions, session creation)
- **Non-admin deployment summary view** (AM/PM session counts, unit breakdown)
- **Non-admin attendance marking view** (inline status/remarks editing, role-aware nav)
- **Estab admin view** (`/admin/estabs`) with CAA date, source filename, personnel count, status
- **`CsvUpload.original_filename`** — upload-time filename now stored (was only in audit log)
- **Estab API** (`GET /api/v1/estabs`, `GET /api/v1/estabs/{id}`) — list/detail with latest CsvUpload join
- **Non-admin estab browser** (`/estab`) — roster table with estab selector, search, unit filter; row-numbered for easy counting
- **`short_id` personnel identity** (2026-06-29) — `pers_no` dropped entirely (no longer imported or stored); replaced with server-minted 8-char base62 `short_id` as the cross-estab person identifier. Migration `c3d4e5f6a7b8` (batch-mode for SQLite). See [docs/SPECIFICATION.md](SPECIFICATION.md) §3.2.1.
- **Deployment creation from estab** (2026-07-01) — GUI modal on `/admin/estabs` for confirmed estabs; API validates estab existence + confirmed status (400 on failure). UI uses military date/time format (YYYYMMDD HHMM) with hardcoded Singapore timezone (+08:00).
- **Estab lifecycle management** (2026-07-01) — `PATCH /api/v1/estabs/{id}` for draft↔confirmed transitions (confirm/unconfirm); `DELETE /api/v1/estabs/{id}` for super_admin-only cascade deletion (draft/confirmed only). Migration `d4e5f6a7b8c9`.
- **Deployment personnel exclusion** (2026-07-01) — New `DeploymentPersonnelExclusion` model; `POST/DELETE /api/v1/deployments/{id}/exclusions` endpoints (draft-only); admin page at `/admin/deployments/{id}/personnel` with checkbox-based multi-row editing, client-side search, batch update, and change tracking. Excluded personnel filtered from all deployment views via shared listing function.
- **Session auto-population** (2026-07-01) — Creating a session now automatically generates AttendanceRecord entries for all active personnel in the deployment's estab (minus exclusions), with status='absent'. Eliminates manual record creation.
- **Attendance status enum simplified** (2026-07-01) — Removed "unknown" status; only "present", "absent", "excused" remain. Default is "absent".
- **Deployment date editing** (2026-07-01) — Admin UI supports editing valid_from/valid_until via inline form. API validates that no sessions fall outside the new date range (returns error if sessions would be orphaned).
- **Admin deployments page enhancements** (2026-07-01) — Auto-expands active deployment on page load, per-session "Update" button linking to /attendance, autofill next session date/type for quick session creation.
- **Attendance page enhancements** (2026-07-01) — Color-coded status dropdown (present=green, absent=red, excused=yellow), sub-unit 1 & 2 columns displayed, column filter and sort support.

### System Capabilities
- Multi-tenant deployment isolation with access control
- Automatic data filtering by deployment scope
- Role-based permissions (super_admin, admin, user)
- Deployment access grants and revocation
- Subunit scope filtering support
- Comprehensive audit trails (user management, CSV uploads, deployment/session transitions) with browsable admin view
- Production deployment guides

---

## Current Phase: Frontend Development (Phase 9) - IN PROGRESS

**Priority:** HIGH
**Status:** Phase 9D (Non-Admin Views) COMPLETE — Phase 9E (Mobile Optimization) NEXT

### Phase 9A: Foundation — COMPLETED

- [x] Set up Jinja2 templates in FastAPI (singleton pattern, cache_size=0)
- [x] Create base template with responsive layout
- [x] Implement Google OAuth login UI flow (login page, OAuth start, callback)
- [x] Host-independent OAuth (dynamic redirect URIs)
- [x] Secure server-side cookie management (httponly, centralized in utils.cookies)
- [x] Protected admin routes with authentication checks
- [x] Logout functionality (no redirect loops)
- [x] 7 admin page templates created (dashboard, deployments, sessions, users, csv-upload, settings, audit)

### Phase 9B: Dashboard Wiring + CSV Upload — COMPLETED

- [x] Dashboard shows real counts (active deployments, open sessions, active personnel, active users)
- [x] Dashboard shows recent audit log activity (last 10 entries with user names)
- [x] CSV upload accepts .csv files with SHA256 hashing and duplicate detection
- [x] CSV upload detects and displays columns
- [x] CSV upload shows previous uploads list
- [x] Added `"id": current_admin.id` to all 7 template user dicts
- [x] 9 integration tests for CSV upload API
- [x] Documentation updated ([ENDPOINTS.md](ENDPOINTS.md))

### Phase 9C-1: User Management — COMPLETED

- [x] User management page with search/filter (name, email, status, role)
- [x] Inline role editing via dropdown (PATCH /api/v1/users/{id})
- [x] Inline status editing via dropdown
- [x] Delete user with confirmation (super_admin only)
- [x] AuditLog entries created on user update and delete
- [x] 3 integration tests for audit log verification

### Phase 9C-2: Audit Log API + Page — COMPLETED

- [x] `GET /api/v1/audit/logs` endpoint with filtering (entity_type, action, target_user_id) and pagination
- [x] Admin page at `/admin/audit` with filter form, colored action badges, pagination footer
- [x] User name/email resolved via left outer join (handles null user_id for system entries)
- [x] 10 integration tests (filtering by entity_type/action/target_user_id, pagination, ordering, permissions, null user_id, user_name resolution)
- [x] Action badges colored by type (red=delete, green=create, yellow=update, purple=archive, blue=close, pink=finalize) — ready for Phase 9C-3 operations

#### Deferred CSV Pipeline Steps (Future Sessions)
- **Step 2:** Column mapping UI (map raw CSV columns to canonical names)
- **Step 3:** Diff confirmation (compare new upload vs current active Estab)
- ColumnMetadata record creation
- Estab creation from CSV data
- Personnel record generation from mapped CSV rows

### Phase 9C-3: Deployment + Session Management — COMPLETED

- [x] Combined admin page at `/admin/deployments` with expandable session sub-views per deployment
- [x] `/admin/sessions` redirects to `/admin/deployments` (Sessions nav link removed)
- [x] Deployment list with status-colored cards, filter by status
- [x] Deployment status transitions: activate, close, archive, finalize (hardcoded action buttons per valid transitions)
- [x] Session sub-view with status badges and action buttons (close, finalize)
- [x] Inline session creation form (date + AM/PM) for draft and active deployments
- [x] Delete (super_admin only) for deployments (blocked if active/finalized) and sessions (blocked if finalized)
- [x] PRD §8 compliance fix: API now allows session creation for draft deployments (was blocked to active-only)
- [x] API stays separate (`/api/v1/deployments/*`, `/api/v1/sessions/*`) — only HTML admin view combined
- [x] 1 new test (draft deployment session creation), 1 updated test (inactive deployment now correctly tested)

### Phase 9D: Non-Admin Views — COMPLETED

**Goal:** User-facing deployment summary and attendance marking views for regular (non-admin) users.

**Completed features:**
- [x] `get_current_user_optional()` auth function (any active authenticated user, no role check)
- [x] `GET /deployment` — deployment summary with AM/PM session counts and unit breakdown
- [x] `GET /attendance` — attendance marking table with inline status/remarks editing
- [x] Role-aware nav in base.html (Deployment/Attendance for all users, admin links conditional on role)
- [x] OAuth callback redirects admins to `/admin`, regular users to `/deployment`
- [x] Login page redirects already-authenticated users to the appropriate view
- [x] Deployment selector dropdown (GET param, page reload) on both views
- [x] Session selector dropdown on attendance view, defaults to most recent open session
- [x] Attendance table disabled (read-only) when session is closed/finalized
- [x] 235 tests passing (no regressions)

**Design decisions (confirmed 2026-06-22):**
- Simple table layout (no complex UI components)
- Fixed columns hardcoded but not position-dependent in code (future-proof for column config)
- Deployment selector dropdown (GET param, page reload)
- Column manifest pattern deferred (depends on CSV Step 2 — column mapping)
- Parade state format deferred (awaiting formal spec from stakeholder post-MVP approval)
- Bulk marking remains admin-only
- Skip graceful empty-state handling for now

**Files created:**
- `src/parade_state/web/deployment.py` — deployment view route (`/deployment`)
- `src/parade_state/web/attendance.py` — attendance view route (`/attendance`)
- `src/parade_state/templates/deployment.html` — deployment summary template
- `src/parade_state/templates/attendance.html` — attendance marking template

**Files modified:**
- `src/parade_state/auth/admin_dependencies.py` — added `get_current_user_optional()`
- `src/parade_state/templates/base.html` — role-aware nav (Deployment/Attendance for all, admin links conditional on `user.role in ['admin', 'super_admin']`)
- `src/parade_state/web/auth.py` — OAuth callback role-aware redirect, login page redirect for regular users
- `src/parade_state/main.py` — registered new web routers

**Deferred items (await CSV Step 2):**
- Column manifest pattern (configurable columns, sensitivity levels, display order)
- Column mapping UI
- Personnel browser (estab-scoped, not deployment-scoped)

### Phase 9E: Mobile Optimization (Future)

**Priority:** Responsive design for field use (tablets, mobile).

---

### Phase 9F: Estab Views — COMPLETED (2026-06-24)

**Goal:** Surface estab data to both admins (management view) and regular users (roster browser).

**Completed features:**
- [x] `CsvUpload.original_filename` column + Alembic migration `a1b2c3d4e5f6`
- [x] `GET /api/v1/estabs` (list) and `GET /api/v1/estabs/{id}` (detail), admin-only, with latest-CsvUpload join for source filename
- [x] `POST /api/v1/csv/upload` stores `original_filename`
- [x] Admin estab management page at `/admin/estabs` (CAA date, source file, personnel count, status filter)
- [x] Non-admin estab browser at `/estab` — roster table with row numbers, estab selector, search, unit filter
- [x] Nav: "Estab" link in user sidebar (between Attendance and Admin section); "Estabs" link in admin sidebar
- [x] 235 tests still passing (no regressions)

**Design decisions:**
- File reference stored on `CsvUpload` (normalized) and surfaced via join in estab views. Denormalization to `Estab` deferred — see Pending Decisions.
- Estab browser is open to all authenticated users (org-wide reference data). Deployment-based subunit scoping is a possible future refinement.

**Files added:**
- `src/parade_state/api/estabs.py`
- `src/parade_state/web/estab.py`
- `src/parade_state/templates/admin/estabs.html`
- `src/parade_state/templates/estab.html`
- `src/parade_state/migrations/versions/a1b2c3d4e5f6_add_original_filename_to_csv_uploads.py`

---

### Pending Decisions

**Estab file-reference convention (2026-06-24).** Each Estab is currently associated with its source file via `CsvUpload.original_filename` (upload-time filename, joined via `estab.csv_uploads`). Open questions:
- Should this be denormalized onto `Estab` directly (e.g., `Estab.source_filename`) for cheaper queries / independent renaming?
- Should the identifier be a filename, a content-addressed hash (`sha256_hash` already exists), or an opaque upload ID?
- Naming: `original_filename` vs `source_filename` vs `source_file_ref`.

Defer until the diff-confirmation step (Phase 9C-2 deferred work) forces a concrete decision.

---

## Deferred Phases

### **Phase 7: Reporting & Analytics** (DEFERRED)
**Why Deferred:** Requires production data to design meaningful reports. Can't build exception reporting without understanding real-world patterns. Will revisit after frontend launches and users generate data.

**Original Plan:** Deployment status reports, CSV export, attendance summaries

**New Timeline:** After Phase 9 completion and production data collection

### **Phase 8: Performance & Scalability** (MEDIUM Priority)
**Focus:** Optimize for growing datasets and increased usage  
**Key Areas:** Database indexing, query optimization, caching layer, background jobs
**Timeline:** After Phase 9, before or during production scaling

---

## Technical Documentation

For detailed information on completed features and system architecture, see:

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System architecture and design decisions
- **[SECURITY.md](SECURITY.md)** - Security patterns and access control
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Production deployment guides
- **[TESTING.md](TESTING.md)** - Testing strategies and approaches
- **[CODE_STYLE.md](CODE_STYLE.md)** - Coding standards and conventions

---

## Implementation History

For detailed implementation history, see git commit log:
```bash
git log --oneline --all
```

**Recent Major Completions:**
- **short_id Refactor** (2026-06-29) - Replaced `Personnel.pers_no` (opaque, sensitive external key, no longer imported or stored) with `Personnel.short_id` — a server-minted 8-char base62 cross-estab person identifier. Added `ids.short_id()` and `ids.mint_unique_short_id()`. Migration `c3d4e5f6a7b8` (uses `batch_alter_table` for SQLite compatibility). Updated API, web/views, schemas, tests, and all docs. 245 tests passing.
- **Phase 9F: Estab Views** (2026-06-24) - Admin estab management page (`/admin/estabs`), non-admin estab browser (`/estab`) with row-numbered roster table and search/unit filter, estab API endpoints, `CsvUpload.original_filename` column + migration `a1b2c3d4e5f6`. File-reference naming convention deferred (see Pending Decisions).
- **Phase 9D: Non-Admin Views** (2026-06-22) - Deployment summary view (`/deployment`) with AM/PM session counts and unit breakdown, attendance marking view (`/attendance`) with inline status/remarks editing, `get_current_user_optional()` auth function, role-aware nav, OAuth callback role-aware redirect
- **Phase 9C-3: Deployment + Session Management** (2026-06-22) - Combined admin page with expandable session sub-views, status transitions, session creation, PRD §8 compliance fix, 1 new + 1 updated test
- **Phase 9C-2: Audit Log API + Page** (2026-06-22) - Audit log API with filtering/pagination, admin page with colored action badges, 10 integration tests
- **Phase 9C-1: User Management** (2026-06-22) - Admin users page with search/filter, inline role/status editing, delete, audit log entries on user update/delete
- **Phase 9B: Dashboard + CSV Upload** (2026-06-22) - Dashboard with real DB queries, CSV file upload API with SHA256 hashing/duplicate detection/column parsing, 9 integration tests
- **Phase 9A: Frontend Foundation** (2026-06-22) - OAuth authentication, Jinja2 templates, admin interface, secure cookies, logout
- **Phase 5: Advanced Access Control** (2026-05-10) - Multi-tenant security
- **Phase 4: Personnel Management** (2026-05-08) - Deployment-based personnel operations
- **Phase 3: Attendance Sessions** (Completed) - AM/PM session management
- **Phase 2: Deployments** (Completed) - Deployment lifecycle management
- **Phase 1: Authentication** (Completed) - Google OAuth and user management

**Priority Changes:**
- **2026-07-01:** Session auto-population, attendance enum simplification, deployment date editing, and UI enhancements shipped. Session creation now auto-generates AttendanceRecord entries for all active personnel (minus exclusions) with status='absent'. Attendance status enum reduced to present/absent/excused (removed "unknown"; default "absent"). Admin deployments page auto-expands active deployment, adds per-session "Update" button linking to /attendance, and autofills next session date/type. Deployment date editing (valid_from/valid_until) via inline form with API validation that no sessions fall outside the new range. Attendance page gains color-coded status dropdown, sub-unit 1 & 2 columns, and column filter/sort.
- **2026-07-01:** Deployment management enhancements complete. Three feature sets shipped: (1) Deployment creation from estab via GUI modal on `/admin/estabs` with API-level estab validation (must exist + be confirmed). (2) Estab lifecycle management — `PATCH` for draft↔confirmed transitions, `DELETE` for super_admin cascade deletion. (3) Deployment personnel exclusion — new `DeploymentPersonnelExclusion` model, draft-only API endpoints, admin page at `/admin/deployments/{id}/personnel` with checkbox-based multi-row editing. Personnel listing function updated to filter excluded personnel from all deployment views. Migration `d4e5f6a7b8c9`. 270 tests passing.
- **2026-06-29:** `short_id` refactor complete. `Personnel.pers_no` dropped (never imported or stored); replaced with server-minted 8-char base62 `short_id` (cross-estab person identity). Migration `c3d4e5f6a7b8` (batch-mode for SQLite). 245 tests passing. Next: mobile optimization (Phase 9E) or diff-confirmation step.
- **2026-06-24:** Phase 9F complete. Admin estab management page (`/admin/estabs`) and non-admin estab browser (`/estab`) shipped. Added `CsvUpload.original_filename` column (migration `a1b2c3d4e5f6`) and `GET /api/v1/estabs` endpoints. Estab browser shows row-numbered roster table with search/unit filter, open to all authenticated users. Next: mobile optimization (Phase 9E) or diff-confirmation step (Phase 9C-2 deferred work).
- **2026-06-22:** Phase 9D complete. Non-admin user-facing views implemented: deployment summary (`/deployment`) with AM/PM session counts and unit breakdown, attendance marking (`/attendance`) with inline status/remarks editing. Added `get_current_user_optional()` auth function. Role-aware nav in base.html. OAuth callback now redirects admins to `/admin` and regular users to `/deployment`. Next: mobile optimization (Phase 9E).
- **2026-06-22:** Phase 9C-3 complete. Combined deployment + session admin page at `/admin/deployments` with expandable sub-views, status transitions, inline session creation. PRD §8 compliance fix (draft deployments can now create sessions). `/admin/sessions` redirects to `/admin/deployments`. Next: mobile optimization (Phase 9C-4) or settings page wiring.
- **2026-06-22:** Phase 9C-2 complete. Audit log API + admin page implemented with filtering (entity_type, action, target_user_id), pagination, and colored action badges. Next: remaining admin pages (attendance marking, personnel browser, deployment management, session controls).
- **2026-06-22:** Phase 9B + 9C-1 complete. Dashboard wired with real data, CSV upload implemented, user management page functional with audit logging. Next: audit log API + page.
- **2026-06-22:** Phase 9A complete. OAuth login/logout working, 7 admin templates created. Next: wire up dashboard with real data and implement CSV upload step 1 (file ingestion).
- **2026-06-22:** Phase 9 (Frontend) prioritized from LOW to HIGH. Admin interface completed with host-independent OAuth flow. Frontend development now critical for user acquisition and production validation.
- **2026-05-16:** Phase 7 reduced to deployment status + CSV export only. Comprehensive reporting deferred pending stakeholder requirements and production data analysis.

---

**Next: Phase 9E — Mobile Optimization**

Session auto-population, attendance enum simplification (removed "unknown"), deployment date editing with session validation, admin deployments page auto-expand and quick-session features, and attendance page color-coding/filter/sort complete (2026-07-01). These enhancements streamline the admin and user workflows for parade state management. Next: responsive design optimization for field use (tablets, mobile), or the diff-confirmation step (Phase 9C-2 deferred work) if CSV pipeline continuation is prioritized.
