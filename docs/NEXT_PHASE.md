# Roadmap & Open Work

**Last Updated:** 2026-08-25
**Status:** In production on Railway (admin-only access), with a separate
hosted development environment (Issue 15) where test users try changes
first. Test users (admins) coming on the weekend of 2026-08-22; annual
intensive-use window ~2026-09-10.

This is the living roadmap. Feature behavior lives in
[SPECIFICATION.md](SPECIFICATION.md), endpoints in [api.yaml](api.yaml),
deployment/ops in [DEPLOYMENT.md](DEPLOYMENT.md) /
[BACKUP_SETUP.md](BACKUP_SETUP.md), and implementation history in git
(PR titles carry the summaries this file used to duplicate).

---

## Current Snapshot

- **Tests:** 670 SQLite passing (flags-on posture; flags-off gating has
  dedicated tests). The suite runs against
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
- **Environments:** production (`main`) and development (`dev`) run as
  separate Railway environments with isolated databases
  ([DEPLOYMENT.md](DEPLOYMENT.md) › Environments). Dev is the empty-start
  playground for test users (purge enabled); promotion to prod is
  PR `dev` → `main`.
- **Feature flags:** `FEATURE_DEFERMENTS` / `FEATURE_GROUPING` env-var
  booleans hide those features entirely (nav, pages, API — 404 for all
  roles, super-admins included) until ready. Currently `false` in dev
  (hidden during the tester window) and unset in prod; flip per feature
  readiness ([DEPLOYMENT.md](DEPLOYMENT.md) › Feature Flags).
  `FEATURE_STRENGTH` (Unit Strength at `/admin`) is on in both
  environments. **Core-feature kill switches** (issue 23):
  `FEATURE_NOMINALROLL` / `FEATURE_ATTENDANCE` default **on** (unset =
  available) and hide their feature entirely only on an explicit `false`
  — the emergency path for taking a shipped core feature offline
  mid-window without a deploy. Both are `true` in dev and prod.
  **Feature-access matrix (issue 37):** a per-role visibility layer
  beneath the env flags — super-admins toggle, per feature, whether the
  `admin` role can see/use it (Settings › Feature access; fail-open:
  absent row = enabled; env flag off outranks the matrix; super-admins
  never restricted). Enforcement spans sidebar + page routes + API
  edges (personnel/nominal-rolls/attendance/groupings). Settings, Users,
  and Restore Backup are hard-gated super-admin surfaces; Audit Log
  stays admin-viewable.
  **Environment banner:** dev sets `ENVIRONMENT_BANNER` so a thin amber
  strip at the top of every page (login included) names the environment;
  prod leaves it unset (zero markup, zero layout impact).

### What the app does today

- Google OAuth sign-in (host-independent), admin-only auth, audit log
- CSV upload → process into Nominal Roll + Personnel + auto-tagging
  (**ingestion contract v2** — header-name matching, strict Yes-only row
  filter, optional Pers/Age(Yr); see SPECIFICATION §4.5); taggings
  importable across NRs by `pers_no`; super-admins can also add a
  missing serviceman manually from the NR view (`source='manual'`, pers_no
  fill-in-later inline; per-roll, not propagated to future CSV rolls)
- Tagging overlay, 1:1 per NR: unit/subunit edits land on the overlay;
  reads serve effective (`to_*`-overlaid) values; CSV-sourced NR data
  itself is read-only; sub-unit 2/3 reallocation is an in-scope-admin
  capability (issue 38) while unit/sub-unit 1 stays super-admin-only,
  enforced at the personnel-PATCH seam (403 for admins, whole payload)
- One system-wide **active-for-attendance** Nominal Roll (super-admin
  switch); `Attendance` rows per (personnel, date) with single-session
  status (present/absent) + optional reason enum + remarks (issue 33);
  writes gated to the active NR; the marking roster is **all** NR personnel
  (deferred included) with a read-only Inpro Status column + filter —
  filtering hides rows non-destructively, existing attendance records are
  preserved; super-admins can **freeze** a day (issue 35) — frozen days
  turn read-only for admins (403 on writes) while super-admins keep
  editing, banner + freeze timestamp visible to every role
- **Admin scoped access (issue #28)**: scope grants are
  (unit, sub_unit_1) pairs per NR on `UserSubunitAssignment` with the
  explicit `*` wildcard sentinel; matching follows the tagging-overlay
  effective location; deny-by-default on every read/write personnel
  surface (personnel list/detail/history/PATCH, attendance list/upsert/
  copy/export, NR list/detail/PATCH/export, strength report, NR browser
  page — client filters only narrow); out-of-scope answers 403 naming
  the missing assignment; grants managed from the /admin/users Scope
  panel (roster-validated) via the access-control API; CSV upload/process
  tightened to super-admin. The board stays org-wide for admins (posts
  carry no NR linkage — recorded decision). Caller identity is
  session-derived on every `/api/v1` endpoint (issue 31 ✅): the shared
  dependencies in `auth/dependencies.py` resolve the session user, the
  spoofable query params are gone, and
  `tests/integration/test_no_client_identity.py` enforces it structurally
  + behaviorally
- **Unit Strength** report at `/admin` (replaced the dashboard): the
  parade state rolled up by effective sub-unit 1/2 into the Officer/WOSE/
  Total × In/Out/Current/% strength format (In = not deferred, Current =
  present — single session, reason never participates, Out = rest); date
  param; super-admin basis toggle tagged/untagged (issue 36 — untagged =
  original NR allocations); regular admins scoped to their assigned
  sub-units; on via `FEATURE_STRENGTH` in dev and prod
- Groupings (issue 26 redesign, implemented on this branch): a labelled
  set of groups on the attendance-active NR with memberships, per-person
  checkbox/remarks, clone, copy-from-previous-NR, slim CSV export —
  super-admin-only mutations, all-role reads, no attendance interaction;
  the old lifecycle/overrides/exclusions/notes/access-scoping design and
  the `/grouping/{id}/personnel` page are gone; **feature-flagged**
  (`FEATURE_GROUPING`, default off)
- Export CSV on all three working views — Grouping (issue 26), and the
  Nominal Roll browser + Attendance marking table (issue 27): each streams
  its displayed table (filters honoured, tagging overlay applied; the NR
  export has no row cap; the attendance export follows the Subunit-1 read
  scoping and labels statuses like the page)
- Deferments (super-admin CRUD; drives `Personnel.inpro_status` — issue 32:
  approve prompts, cancel/delete revert to yet_to_inpro);
  **feature-flagged** (`FEATURE_DEFERMENTS`, dev-only until ready)
- Discussions board (issue 24): admins/super-admins post `requests` /
  `bugs` items and comment in sanitized markdown; super-admins triage
  (status, category — the only audit-logged board action) and delete;
  author-only edits enforced server-side from the session identity;
  **feature-flagged** (`FEATURE_DISCUSSIONS`, default off)
- Sidebar: workflow pages flat (Upload NR, Nominal Roll, Taggings,
  Deferments, Attendance, Unit Strength, Grouping, Discussions — Unit
  Strength moved after Attendance 2026-08-26) + **Admin** section
  (Users, Settings, Audit Log, Restore Backup); SA-only pages show an
  in-page no-access message for plain admins; flag-gated entries
  (Deferments, Grouping, Discussions, Unit Strength) render only when
  their flag is on
- Admin UI: Unit Strength, users, audit log, taggings, deferments,
  Upload NR (CSV upload), DB restore, Settings purge (testing-only);
  NR management lives in an expander on its view, grouping management on
  the Grouping page
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
need during the window. The first such request — the unit's strength
reporting spreadsheet — is already shipped as the Unit Strength page
(Issue 25); CSV export of it can follow if wanted.

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

The process endpoint uses the fixed header-name contract v2 (issue 34,
`parade_state.utils.csv_constants`): required/optional columns matched by
exact header name with a strict Yes-only row filter. Only this one export
format is in play this season, so generalizing to admin-configurable
mappings waits until post-season.

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

- **2026-08-27:** Admin sub-unit 2/3 reallocation (Issue 38): NR browser
  sub-unit 2/3 cells editable for in-scope admins (same staged-edit
  flow; unit/sub-unit 1 read-only, their suggestion lists unshipped);
  `PATCH /api/v1/personnel/{id}` 403s `unit`/`sub_unit_1` for
  non-super-admins before the scope gate and any mutation (mixed
  payloads rejected whole, no tagging entry written) — closes the
  pre-38 API hole where the UI was the only gate; taggings CRUD stays
  super-admin-only
- **2026-08-27:** Feature-access matrix (Issue 37): `feature_access`
  table (migration `y6f7a8b9c0d1` + widened `audit_entity_type`);
  super-admin "Feature access (admins)" card in Settings →
  `POST /api/v1/admin/feature-access` (full-matrix upsert, audit-logged);
  effective visibility = env flag AND matrix, fail-open, super-admins
  bypass; enforced at sidebar (per-request middleware snapshot), page
  routes (styled 403), and admin-reachable API edges
  (personnel/nominal-rolls/attendance/groupings → 403); Settings itself
  now super-admin-only; Users/Settings/Restore Backup sidebar entries
  hidden from plain admins (Audit Log stays viewable)
- **2026-08-26:** Sidebar reorder (direct to dev): Unit Strength moved
  after Attendance — reporting follows marking
- **2026-08-26:** Attendance local-day default: `/attendance` server
  default for the viewed day is UTC today; a first-visit script (no
  `?date=` in the URL) re-defaults to the browser's local day and
  resubmits the filter form (filters preserved) — UTC was showing
  yesterday until 08:00 SGT; explicit `?date=` and no-JS clients keep
  the server default. Same pattern as the strength report's date picker
- **2026-08-25:** Unit Strength basis toggle (Issue 36): `?basis=tagged|
  untagged` on `/admin` (default tagged, unknown → tagged); untagged
  groups under the original NR allocations (canonical Personnel columns,
  overlay not applied, `from_*` never consulted) — super-admin-only
  (segmented Tagged/Untagged control rendered for them alone; other
  roles requesting it get
  the 403 no-access page); heading names the basis; unit TOTAL identical
  on both bases; no schema/API changes
- **2026-08-25:** Attendance day freeze (Issue 35): `attendance_freezes`
  table (one row per frozen NR/day, migration `x5e6f7a8b9c0` + widened
  `audit_action`); super-admin-only `PUT/DELETE /api/v1/attendance/freeze`
  (active-NR gated; 409 double-freeze / 404 unfreeze-miss; audit-logged
  action `attendance_freeze`); upsert + copy-remarks 403 naming the
  freeze when non-super-admins write a frozen day (super-admins keep
  editing, retro rules unchanged); page: Freeze/Unfreeze toggle
  (super-admins), frozen banner with timestamp (all roles), read-only
  plain-text grid for admins — reads/exports/scope never blocked
- **2026-08-25:** Two deploy-day hotfixes after the r20260825 switch:
  (1) the Docker image's Python 3.12 eagerly evaluates annotations, so
  issue 31's `user: User` signature without the import crash-looped the
  app at startup (local 3.14's lazy annotations hid it) — import added;
  pre-deploy guard: `uv run -p 3.12` app import. (2) The issue-31
  template sweep corrupted five fetch calls (missing comma turned the
  staged-remap Apply into a 404ing GET; four unterminated strings killed
  the Taggings/Deferments page JS) — all repaired; template JS has no
  automated coverage (a node --check sweep over script blocks catches
  the syntax class).
- **2026-08-25:** CSV ingestion contract v2 (Issue 34): header-name
  matching replaces the index-based 18-column map — required columns
  (Unit incl. non-blank header, Sub Unit 1-3, Rank, Full Name, Callup
  Decision, Reason, Remarks, ORNS/ORNS-Yrs alias, HK ICT) validated with
  named-column 400s; optional Pers (→ pers_no, blank/absent → NULL) and
  Age(Yr) (→ extra_fields.age_yr); strict Yes-only row filter with
  decision-skip counts (`decision_skipped`) in the process report; Reason
  + Callup Decision read but never stored (issue-32 interim shim removed);
  first Remarks column only; extra columns tolerated and ignored
  (extra_fields carries just orns/hk_ict/age_yr); pre-v2 16-column export
  ingests cleanly under the converged contract; canonical fixture =
  397 stored / 163 skipped; demo ingester + demo DB regenerated
- **2026-08-25:** Attendance single-session rework (Issue 33): the 9-value
  AM/PM vocabulary collapsed to one daily session — `status`
  (present/absent, default absent) + nullable `reason` enum
  (mc/off/early_outpro/other/awol, classifies remarks, never feeds
  reporting) + single `remarks` (migration `w4d5e6f7a8b9`; PM mapping wins
  when its slot was marked, remarks joined `"; "`, "Late" appended); the
  issue-32 interim roster gate is gone — the marking page lists **all** NR
  personnel with a read-only Inpro Status column + filter, and the export
  mirrors it (…, Inpro Status, Status, Reason, Remarks); Copy Remarks
  became date→date; Unit Strength Current = present only (slot selector
  removed); attendance-history stats count days
- **2026-08-21:** Discussions board (issue 24): admins post `requests`/
  `bugs` items, comment in sanitized markdown (raw HTML escaped, unsafe
  link schemes scrubbed); super-admin triage (category/status, the only
  audit-logged action) + deletions; author-only edits enforced
  server-side from the session identity; `FEATURE_DISCUSSIONS`-gated,
  default off
- **2026-08-20:** Add Serviceman (Issue 26): super-admin manual personnel
  creation from the NR view — `Personnel.source` provenance ('manual'
  badge), `POST /api/v1/personnel`, pers_no nullable + super-admin
  fill-in-later PATCH (inline cell); per-roll only (no propagation)
- **2026-08-20:** Attendance autosave (Issue 19): Save button removed, rows
  PUT themselves on status change / remarks blur with a Saving…/Saved
  indicator and a red-edge retry state on failure; yellow tagged-row
  highlight now only in the NR view
- **2026-08-20:** Copy Remarks modal (Issue 20): explicit source/destination
  (day + AM/PM) with plain-language confirmation, sub-unit view filter
  respected server-side; button open to all admins (write perms enforced
  per sub-unit); endpoint takes explicit source/dest params (old
  time-of-day logic survives as the modal prefill)
- **2026-08-20:** Unit Strength report (Issue 25) at `/admin` (replaces the
  dashboard): parade state aggregated by effective sub-unit into the
  Officer/WOSE/Total × In/Out/Current/% reporting format; date + AM/PM
  slot selector; subunit-scoped for regular admins; `FEATURE_STRENGTH`-gated
- **2026-08-20:** NR status & remarks columns (Issue 06, vastly simplified
  from the funnel model): `callup_status` widened to six values + per-person
  `remarks`; CSV `Callup Decision`/`Reason`/`Remarks` mapped on ingest;
  attendance view shows only Called Up (non-destructive); inline admin
  editing in the NR browser — superseded 2026-08-24 by the issue 32 inpro
  rework directly below
- **2026-08-24:** Inpro status rework (Issue 32): `callup_status` renamed to
  `inpro_status` with the 3-value lifecycle `inproed` / `yet_to_inpro`
  (default) / `deferred` (migration `v3c4d5e6f7a8`; Disrupted/MR/Age
  Limit/Other → yet_to_inpro + `Previously:` remark); attendance roster,
  export and Unit Strength now gate on `!= 'deferred'` until #33; NR
  browser column renamed "Inpro Status" with a user-side filter; deferment
  approval prompts instead of auto-deferring, cancel/delete revert to
  yet_to_inpro; interim CSV shim keeps old-format uploads working until
  #34's new format (fixture profiling: the real Callup Decision columns
  carry Yes/No — Yes → yet_to_inpro, No → deferred; the retired enum
  vocabulary is still honoured for older files)
- **2026-08-20:** Groupings redesigned (issue 26): the old
  modes/lifecycle/overrides/exclusions/notes/access-scoping design was
  replaced wholesale with a labelled set of groups per nominal roll —
  memberships, per-person checkbox/remarks, clone,
  copy-from-previous-NR, slim CSV export; super-admin-only mutations,
  all-role reads; no attendance interaction; `FEATURE_GROUPING` still
  default off
- **2026-08-20:** Environment banner: `ENVIRONMENT_BANNER` renders a thin
  fixed top strip on every page (login included) naming the environment;
  set in dev, unset in prod — pure overlay, page below pixel-identical
- **2026-08-20:** Env-var feature flags (Issue 18): Deferments and
  Grouping hidden entirely (nav, pages, API — 404 for all roles
  including super-admins) until ready; enabled in dev via Railway env
  vars, off in prod
- **2026-08-20:** Hosted development environment stood up on Railway
  (Issue 15): separate `development` environment + Postgres tracking the
  `dev` branch, empty-start DB with purge enabled; test users use dev
  first, prod stays baseline
- **2026-08-20:** NR browser cell edits staged client-side with an
  Apply/Discard bar (Issue 17) — misclick-safe, refresh-persistent
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
