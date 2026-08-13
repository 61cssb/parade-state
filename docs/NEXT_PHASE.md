# Next Implementation Phase

**Last Updated:** 2026-07-02 (Deferments MVP)
**Status:** Production-Ready Backend with User-Facing Views

---

## Current System Status

### Production-Ready Metrics
- 292 tests passing (100% pass rate)
- 57 API endpoints fully implemented and tested
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
- **Nominal Roll admin view** (`/admin/nominal-rolls`) with CAA date, source filename, personnel count, status
- **`CsvUpload.original_filename`** — upload-time filename now stored (was only in audit log)
- **Nominal Roll API** (`GET /api/v1/nominal-rolls`, `GET /api/v1/nominal-rolls/{id}`) — list/detail with latest CsvUpload join
- **Non-admin nominal roll browser** (`/nominal-roll`) — roster table with nominal roll selector, search, unit filter; row-numbered for easy counting
- **`short_id` personnel identity** (2026-06-29) — `pers_no` dropped entirely (no longer imported or stored); replaced with server-minted 8-char base62 `short_id` as the cross-roll person identifier. Migration `c3d4e5f6a7b8` (batch-mode for SQLite). See [docs/SPECIFICATION.md](SPECIFICATION.md) §3.2.1.
- **Deployment creation from nominal roll** (2026-07-01) — GUI modal on `/admin/nominal-rolls` for confirmed nominal rolls; API validates nominal roll existence + confirmed status (400 on failure). UI uses military date/time format (YYYYMMDD HHMM) with hardcoded Singapore timezone (+08:00).
- **Nominal Roll lifecycle management** (2026-07-01) — `PATCH /api/v1/nominal-rolls/{id}` for draft↔confirmed transitions (confirm/unconfirm); `DELETE /api/v1/nominal-rolls/{id}` for super_admin-only cascade deletion (draft/confirmed only). Migration `d4e5f6a7b8c9`.
- **Deployment personnel exclusion** (2026-07-01) — New `DeploymentPersonnelExclusion` model; `POST/DELETE /api/v1/deployments/{id}/exclusions` endpoints (draft-only); admin page at `/admin/deployments/{id}/personnel` with checkbox-based multi-row editing, client-side search, batch update, and change tracking. Excluded personnel filtered from all deployment views via shared listing function.
- **Session auto-population** (2026-07-01) — Creating a session now automatically generates AttendanceRecord entries for all active personnel in the deployment's nominal roll (minus exclusions), with status='absent'. Eliminates manual record creation.
- **Attendance status enum simplified** (2026-07-01) — Removed "unknown" status; only "present", "absent", "excused" remain. Default is "absent".
- **Attendance status enum replaced with operational reporting categories** (2026-08-13) — New 9-value enum: `present`, `absent`, `time_off`, `mc`, `yet_to_inpro`, `outpro`, `reporting_sick`, `late`, `att_out` (default `absent`). Legacy `excused` and stray `unknown` rows migrated to `absent` with warning log. Aggregation schemas (`DeploymentStatusSessionInfo`, `DeploymentStatusUnitBreakdown`, `PersonnelAttendanceHistoryStats`) now bucket statuses into present-like (`present`, `late`) vs absent-like (everything else) and drop the `excused` count field. Migration `g7b8c9d0e1f2` (batch mode for SQLite); `env.py` now sets `render_as_batch=True` so future SQLite schema changes can use `batch_alter_table` uniformly.
- **Deployment date editing** (2026-07-01) — Admin UI supports editing valid_from/valid_until via inline form. API validates that no sessions fall outside the new date range (returns error if sessions would be orphaned).
- **Admin deployments page enhancements** (2026-07-01) — Auto-expands active deployment on page load, per-session "Update" button linking to /attendance, autofill next session date/type for quick session creation.
- **Attendance page enhancements** (2026-07-01) — Color-coded status dropdown (present=green, absent=red, excused=yellow), sub-unit 1 & 2 columns displayed, column filter and sort support.
- **Deferments MVP** (2026-07-02) — Super-admin-only deferment CRUD at `/admin/deferments` and `/api/v1/deferments`. New `Personnel.callup_status` field (`Called Up` / `Not Called Up` / `Deferred`). Approving a deferment flips the linked personnel to `Deferred`; reverting from Approved returns to `Called Up` (except for neutral statuses `Not called up` / `Do not call up`). `rank_name` and `sub_unit` snapshotted at creation. Migration `e5f6a7b8c9d0`; existing demo DB backfilled to `Called Up`. User-type scoping deferred to a later phase.

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
- **Step 3:** Diff confirmation (compare new upload vs current active Nominal Roll)
- ColumnMetadata record creation
- Nominal Roll creation from CSV data
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
- Personnel browser (nominal roll-scoped, not deployment-scoped)

### Phase 9E: Mobile Optimization (Future)

**Priority:** Responsive design for field use (tablets, mobile).

---

### Phase 9F: Nominal Roll Views — COMPLETED (2026-06-24)

**Goal:** Surface nominal roll data to both admins (management view) and regular users (roster browser).

**Completed features:**
- [x] `CsvUpload.original_filename` column + Alembic migration `a1b2c3d4e5f6`
- [x] `GET /api/v1/nominal-rolls` (list) and `GET /api/v1/nominal-rolls/{id}` (detail), admin-only, with latest-CsvUpload join for source filename
- [x] `POST /api/v1/csv/upload` stores `original_filename`
- [x] Admin nominal roll management page at `/admin/nominal-rolls` (CAA date, source file, personnel count, status filter)
- [x] Non-admin nominal roll browser at `/nominal-roll` — roster table with row numbers, nominal roll selector, search, unit filter
- [x] Nav: "Nominal Roll" link in user sidebar (between Attendance and Admin section); "Nominal Rolls" link in admin sidebar
- [x] 235 tests still passing (no regressions)

**Design decisions:**
- File reference stored on `CsvUpload` (normalized) and surfaced via join in nominal roll views. Denormalization to `Nominal Roll` deferred — see Pending Decisions.
- Nominal Roll browser is open to all authenticated users (org-wide reference data). Deployment-based subunit scoping is a possible future refinement.

**Files added:**
- `src/parade_state/api/nominal_rolls.py`
- `src/parade_state/web/nominal-roll.py`
- `src/parade_state/templates/admin/nominal-rolls.html`
- `src/parade_state/templates/nominal-roll.html`
- `src/parade_state/migrations/versions/a1b2c3d4e5f6_add_original_filename_to_csv_uploads.py`

---

### Pending Decisions

**Nominal Roll file-reference convention (2026-06-24).** Each Nominal Roll is currently associated with its source file via `CsvUpload.original_filename` (upload-time filename, joined via `nominal roll.csv_uploads`). Open questions:
- Should this be denormalized onto `Nominal Roll` directly (e.g., `NominalRoll.source_filename`) for cheaper queries / independent renaming?
- Should the identifier be a filename, a content-addressed hash (`sha256_hash` already exists), or an opaque upload ID?
- Naming: `original_filename` vs `source_filename` vs `source_file_ref`.

Defer until the diff-confirmation step (Phase 9C-2 deferred work) forces a concrete decision.

---

### Phase 9G: UI Test Automation (Deferred — pending UI stabilization)

**Trigger:** Implement once the UI flow is stable and we're no longer making frequent changes to templates/interactions. Adding earlier would mean rewriting tests on every UI iteration.

**Why:** PRs #3 and #4 required extensive manual UI testing — clicking through modals, buttons, filters, and dropdowns to verify behavior that the existing API-level tests don't cover. The UI is server-rendered Jinja2 + inline `fetch()` calls, so most of this is cheaply automatable.

#### Tier 1: Page-rendering + flow tests (pytest + TestClient) — LOW COST

Expand the existing `client` fixture to test page routes end-to-end: GET page → assert rendered HTML → POST API → GET page → assert updated state. Lives in `tests/behavioral/`. Covers roughly 70% of the manual test load:

- Session creation auto-populates `/attendance` with absent records
- Deployment date edits → 400 (invalid) / 200 (valid) with updated page state
- Excluded personnel absent from non-admin deployment view
- Nominal Roll modal button rendered conditionally on confirmed status
- Nominal Roll confirm/unconfirm/delete buttons → page re-renders correctly
- Checkbox exclusion UI on `/admin/deployments/{id}/personnel` renders expected rows

#### Tier 2: Client-side JS behavior (Playwright Python) — NEW DEPENDENCY

For tests that need a real browser to execute JavaScript. Lives in `tests/e2e/`, gated behind `@pytest.mark.e2e` so default `uv run pytest` stays fast and CI can opt in:

- Color-coded status dropdown updates on change
- Filter and sort on attendance table
- Autofill suggests next session by time of day
- Modal open/close and form submission flows

#### Tier 3: Visual regression (screenshot diffing) — INDEFINITELY DEFERRED

Overkill until a real visual regression problem emerges.

**Dependencies:** None blocking. Can be implemented at any point after the UI stabilizes. Source manual-test checklists live in PRs #3 and #4.

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
- **Attendance Model Rework — PR 2 (Issue #4)** (2026-08-14) - Added NR-scoped Subunit-1 attendance access: new `UserSubunitAssignment(user_id, nominal_roll_id, sub_unit_1)` model. Server-enforced 403 on attendance upsert and copy-remarks when the caller lacks an assignment for a target personnel's *effective* sub_unit_1 (tagging-aware — follows the active Tagging's `to_sub_unit_1`, falling back to canonical). `super_admin` bypasses; deny-by-default. Super-admin CRUD API under `/api/v1/access-control/...`. Reusable enforcement helper in `api/subunit_access.py`. Migration `k1f2a3b4c5d6`. **PR 3 (admin attendance page + scope-activation UI + Copy Remarks button) forthcoming.** 332 tests passing.
- **Attendance Model Rework — PR 1 (Issue #4)** (2026-08-14) - Restructured attendance to attach to a Nominal Roll / Tagging scope with hardcoded AM/PM slots. New `Attendance` (one row per `(personnel, date)`; `status_am`/`remarks_am` + `status_pm`/`remarks_pm`) and `AttendanceScope` (1:1 with NR; the active NR-or-Tagging scope) models. Removed the user-managed `Session` model — `/api/v1/sessions/*` returns 410 Gone. Active-scope gating: a super-admin activates a scope per NR before attendance can be recorded (`PUT /api/v1/attendance/scope/{nr_id}`). New endpoints: `GET /api/v1/attendance/`, `PUT /api/v1/attendance/upsert`, `POST /api/v1/attendance/copy-remarks` (PM-prev-day → AM before noon; AM-same-day → PM after noon). Tagging delete guarded (409) when linked to attendance or set as active scope. Data migration `j0e1f2a3b4c5` merges legacy AM/PM `attendance_records` into the new shape and drops `sessions`/`attendance_records`. User-facing `/attendance` and `/deployment` views rewired to AM/PM; admin deployments page session UI removed. 321 tests passing.
- **Tagging Overlay (Issue #3)** (2026-08-13) - Introduced the Tagging overlay: `Tagging` (globally-unique label, NR FK CASCADE) + `TaggingEntry` (one person → subunit remap per tagging; 4-string `from_*`/`to_*` tuple mirroring `DeploymentPersonnelOverride`). Overlay semantics — creating/editing/deleting a tagging never mutates the underlying NR. `POST /api/v1/taggings/{id}/clone` matches source personnel to target-NR rows by `Personnel.short_id` (the cross-roll person identifier); unmatched source personnel surfaced in the response. Super-admin-only API + admin UI at `/admin/taggings`. Migration `i9d0e1f2a3b4`. 360 tests passing.
- **Personnel Category (Issue #10)** (2026-08-13) - Added `Personnel.category` column (`Officer` / `WOSE`), inferred from rank at ingest time via `parade_state.utils.ranks`. ME1-ME3 classify as WOSE, ME4+ as Officer. Migration `h8c9d0e1f2a3` adds the nullable column; `nr_demo.db` regenerated (no backfill). Category filter on `GET /api/v1/personnel`, on `/nominal-roll`, and on `/admin/deployments/{id}/personnel`. PATCHing rank recomputes category; category is never manually editable. 340 tests passing.
- **short_id Refactor** (2026-06-29) - Replaced `Personnel.pers_no` (opaque, sensitive external key, no longer imported or stored) with `Personnel.short_id` — a server-minted 8-char base62 cross-roll person identifier. Added `ids.short_id()` and `ids.mint_unique_short_id()`. Migration `c3d4e5f6a7b8` (uses `batch_alter_table` for SQLite compatibility). Updated API, web/views, schemas, tests, and all docs. 245 tests passing.
- **Phase 9F: Nominal Roll Views** (2026-06-24) - Admin nominal roll management page (`/admin/nominal-rolls`), non-admin nominal roll browser (`/nominal-roll`) with row-numbered roster table and search/unit filter, nominal roll API endpoints, `CsvUpload.original_filename` column + migration `a1b2c3d4e5f6`. File-reference naming convention deferred (see Pending Decisions).
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
- **2026-08-13:** Tagging overlay (Issue #3) shipped. New `Tagging` + `TaggingEntry` entities model an overlay of person → subunit remappings on top of a Nominal Roll — never mutating the NR's personnel/subunit data. `TaggingEntry` uses the same 4-string `from_*`/`to_*` subunit tuple as `DeploymentPersonnelOverride`; `from_*` is auto-snapshotted from the linked personnel when omitted. `POST /api/v1/taggings/{id}/clone` matches source personnel to target-NR rows by `Personnel.short_id` (the cross-roll person identifier); unmatched source personnel are surfaced. Label uniqueness server-enforced (409 on duplicate). Super-admin-only API + admin UI at `/admin/taggings` with per-person remap picker and clone modal. Migration `i9d0e1f2a3b4`. 360 tests passing.
- **2026-08-13:** Personnel category (Issue #10) shipped. New `Personnel.category` column (`Officer` / `WOSE`) inferred from rank via `parade_state.utils.ranks.category_for_rank()`. ME1-ME3 → WOSE, ME4+ → Officer. Migration `h8c9d0e1f2a3` adds the column nullable (no backfill — demo DB regenerated). Ingest skips and reports rows with unrecognized ranks. Category filter added to `GET /api/v1/personnel`, `/nominal-roll`, and `/admin/deployments/{id}/personnel`. PATCHing rank recomputes category (rejected with 400 on unrecognized rank). Category is always inferred, never manually editable. 340 tests passing.
- **2026-07-01:** Session auto-population, attendance enum simplification, deployment date editing, and UI enhancements shipped. Session creation now auto-generates AttendanceRecord entries for all active personnel (minus exclusions) with status='absent'. Attendance status enum reduced to present/absent/excused (removed "unknown"; default "absent"). Admin deployments page auto-expands active deployment, adds per-session "Update" button linking to /attendance, and autofills next session date/type. Deployment date editing (valid_from/valid_until) via inline form with API validation that no sessions fall outside the new range. Attendance page gains color-coded status dropdown, sub-unit 1 & 2 columns, and column filter/sort.
- **2026-07-01:** Deployment management enhancements complete. Three feature sets shipped: (1) Deployment creation from nominal roll via GUI modal on `/admin/nominal-rolls` with API-level nominal roll validation (must exist + be confirmed). (2) Nominal Roll lifecycle management — `PATCH` for draft↔confirmed transitions, `DELETE` for super_admin cascade deletion. (3) Deployment personnel exclusion — new `DeploymentPersonnelExclusion` model, draft-only API endpoints, admin page at `/admin/deployments/{id}/personnel` with checkbox-based multi-row editing. Personnel listing function updated to filter excluded personnel from all deployment views. Migration `d4e5f6a7b8c9`. 270 tests passing.
- **2026-06-29:** `short_id` refactor complete. `Personnel.pers_no` dropped (never imported or stored); replaced with server-minted 8-char base62 `short_id` (cross-roll person identity). Migration `c3d4e5f6a7b8` (batch-mode for SQLite). 245 tests passing. Next: mobile optimization (Phase 9E) or diff-confirmation step.
- **2026-06-24:** Phase 9F complete. Admin nominal roll management page (`/admin/nominal-rolls`) and non-admin nominal roll browser (`/nominal-roll`) shipped. Added `CsvUpload.original_filename` column (migration `a1b2c3d4e5f6`) and `GET /api/v1/nominal-rolls` endpoints. Nominal Roll browser shows row-numbered roster table with search/unit filter, open to all authenticated users. Next: mobile optimization (Phase 9E) or diff-confirmation step (Phase 9C-2 deferred work).
- **2026-06-22:** Phase 9D complete. Non-admin user-facing views implemented: deployment summary (`/deployment`) with AM/PM session counts and unit breakdown, attendance marking (`/attendance`) with inline status/remarks editing. Added `get_current_user_optional()` auth function. Role-aware nav in base.html. OAuth callback now redirects admins to `/admin` and regular users to `/deployment`. Next: mobile optimization (Phase 9E).
- **2026-06-22:** Phase 9C-3 complete. Combined deployment + session admin page at `/admin/deployments` with expandable sub-views, status transitions, inline session creation. PRD §8 compliance fix (draft deployments can now create sessions). `/admin/sessions` redirects to `/admin/deployments`. Next: mobile optimization (Phase 9C-4) or settings page wiring.
- **2026-06-22:** Phase 9C-2 complete. Audit log API + admin page implemented with filtering (entity_type, action, target_user_id), pagination, and colored action badges. Next: remaining admin pages (attendance marking, personnel browser, deployment management, session controls).
- **2026-06-22:** Phase 9B + 9C-1 complete. Dashboard wired with real data, CSV upload implemented, user management page functional with audit logging. Next: audit log API + page.
- **2026-06-22:** Phase 9A complete. OAuth login/logout working, 7 admin templates created. Next: wire up dashboard with real data and implement CSV upload step 1 (file ingestion).
- **2026-06-22:** Phase 9 (Frontend) prioritized from LOW to HIGH. Admin interface completed with host-independent OAuth flow. Frontend development now critical for user acquisition and production validation.
- **2026-05-16:** Phase 7 reduced to deployment status + CSV export only. Comprehensive reporting deferred pending stakeholder requirements and production data analysis.

---

**Next: Phase 9E — Mobile Optimization**

Session auto-population, attendance enum simplification (removed "unknown"), deployment date editing with session validation, admin deployments page auto-expand and quick-session features, and attendance page color-coding/filter/sort complete (2026-07-01). These enhancements streamline the admin and user workflows for parade state management. Next: responsive design optimization for field use (tablets, mobile), the diff-confirmation step (Phase 9C-2 deferred work) if CSV pipeline continuation is prioritized, or Phase 9G (UI test automation) once the UI flow is stable.
