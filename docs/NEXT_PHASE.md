# Roadmap & Open Work

**Last Updated:** 2026-08-20
**Status:** In production on Railway (admin-only access). Test users
(admins) coming on the weekend of 2026-08-22; annual intensive-use
window ~2026-09-10.

This is the living roadmap. Feature behavior lives in
[SPECIFICATION.md](SPECIFICATION.md), endpoints in [api.yaml](api.yaml),
deployment/ops in [DEPLOYMENT.md](DEPLOYMENT.md) /
[BACKUP_SETUP.md](BACKUP_SETUP.md), and implementation history in git
(PR titles carry the summaries this file used to duplicate).

---

## Current Snapshot

- **Tests:** 438 SQLite passing. The suite runs against
  Postgres by setting `TEST_DATABASE_URL` (per-test databases).
- **Access model:** `super_admin` + `admin` only. Unknown Google
  sign-ins auto-register as `unrecognised` (no access, no session);
  suspended accounts get 403 at the callback. Promotion happens via
  `/admin/users`.
- **Ops:** nightly age-encrypted `pg_dump` backups to the super-admin's
  Google Drive (30-day retention); super-admin UI database restore at
  `/admin/database-restore` (verify-then-swap; production-validated
  2026-08-19 including the older-dump migration path). `RESTORE_ENABLED`
  kill switch. Testing-only super-admin **data purge** at Settings
  (`/admin/settings`, deletes all NRs + downstream data; audit-logged;
  `PURGE_ENABLED` gate, default off in production).

### What the app does today

- Google OAuth sign-in (host-independent), admin-only auth, audit log
- CSV upload → process into Nominal Roll + Personnel + auto-tagging
  (fixed canonical column map from the WY2627 fixture — see CSV Step 2);
  taggings importable across NRs by `pers_no`
- Tagging overlay, 1:1 per NR: unit/subunit edits land on the overlay;
  reads serve effective (`to_*`-overlaid) values; CSV-sourced NR data
  itself is read-only
- One system-wide **active-for-attendance** Nominal Roll (super-admin
  switch); `Attendance` rows per (personnel, date) with AM/PM
  status + remarks; writes gated to the active NR
- Attendance access control by effective sub-unit 1
  (`UserSubunitAssignment`; deny-by-default; super_admin bypasses)
- Groupings: lifecycle, personnel exclusions/overrides, date editing —
  managed from the `/grouping` view's expander (admin groupings page
  retired)
- Deferments (super-admin CRUD; `Personnel.callup_status`)
- Sidebar in two sections — **ICT** (Nominal Roll, Upload NR,
  Attendance, Grouping, Taggings, Deferments) and **Admin** (Dashboard,
  Users, Settings, Audit Log, DB Restore); SA-only pages show an
  in-page no-access message for plain admins
- Admin UI: dashboard, users, audit log, taggings, deferments,
  Upload NR (CSV upload), DB restore, Settings purge (testing-only);
  NR and grouping management live in expanders on their views
- User-facing views — `/grouping`, `/attendance`, `/nominal-roll` —
  built, but admin-gated pending the viewer role (below)

---

## Prioritized Open Work

### 1. Mobile optimization (Phase 9E) — next up, for field use during the window

Responsive design for tablets/phones. Original phase plan applies.

### 2. UI test automation, Tier 1 — before the 2026-09-10 window

Behavioral page tests via the existing `client` fixture (GET page →
assert HTML → POST API → assert state) in `tests/behavioral/`. Covers
most of the manual-test burden that PRs #3/#4 exposed. Tier 2
(Playwright, `@pytest.mark.e2e`) once flows settle; Tier 3 (visual
regression) indefinitely deferred.

### 3. Reporting & analytics (Phase 7) — blocked on format requests from test users

Exception/summary reporting needs real usage patterns. First step:
collect format requests from the test users (admins) coming on the
weekend of 2026-08-22, then design reports around what they actually
need during the window.

### 4. CSV Step 3: diff confirmation — after the season (2026)

When a new CSV arrives for a unit with an existing NR, compare it
against the previous NR (personnel added / removed / changed: rank,
name, sub-unit, pers_no) and require confirmation before committing —
the safety net against wrong or partial roster files. Currently a new
upload replaces the roster wholesale (SHA256 dedupe is the only
guard). This step also forces the file-reference decision below.
Deferred: one NR this season, admins at the wheel, and the
process endpoint's tagging import already covers cross-season
carry-over.

### 5. CSV Step 2: column mapping — after the season (2026)

The process endpoint uses the fixed canonical map from the WY2627 ICT
fixture (`parade_state.utils.csv_constants`). Only one NR format is in
play this season, so generalizing to arbitrary fixtures waits until
post-season.

### 6. Performance & scalability (Phase 8) — as data grows

Indexing, query optimization, caching, background jobs. Post-window if
volumes justify it.

### 7. Deferments user-type scoping — minor

Deferment CRUD is super-admin-only with no user-type scoping; extend
when real deferment workflows emerge.

### 8. Viewer role — deferred until regular non-admin users exist

Open `/grouping`, `/attendance`, `/nominal-roll` to a non-admin role.
Routes and views already exist; the work is the role decision (new
`viewer` status vs reusing the promoted flow), route gating, nav, and
attendance permissions (subunit-1 scoping already exists). This
season's test users will be admins, so this waits.

---

## Pending Decisions

**Nominal Roll file-reference convention (open since 2026-06-24).** NRs
reference their source file via `CsvUpload.original_filename` (joined).
Open questions: denormalize onto `NominalRoll`? filename vs
content-hash (`sha256_hash` exists) vs opaque upload ID? naming?
Defer until CSV Step 3 (diff confirmation) forces it.

---

## Recent History (one line each; git log is authoritative)

- **2026-08-20:** Sidebar restructured into ICT/Admin sections; NR and
  grouping admin pages merged into their views (Issue 07)
- **2026-08-19:** In-app DB restore shipped (PR #38); post-restore
  migration fixed to run in-process after the first production test
  (PR #40); restore button states fixed (PR #39)
- **2026-08-19:** Postgres suite validation + nightly encrypted
  Drive backup pipeline (Issue 14, PRs #31–#37)
- **2026-08-16:** Production hardening (Issue 13); admin-only
  authentication (Issue 12)
- **2026-08-15:** `pers_no` became the canonical personnel identifier
  (Issue 09; `short_id` removed)
- **2026-08-14:** Attendance model rework — NR/tagging scope, AM/PM
  rows, subunit access (Issue #4); tagging 1:1 model (9X); active-NR
  attendance (9Y)
- **2026-08-13:** Tagging overlay (Issue #3); personnel category
  inferred from rank (Issue 10)
