# Roadmap & Open Work

**Last Updated:** 2026-08-19
**Status:** In production on Railway (admin-only access). Test users
(non-admin) targeted for the weekend of 2026-08-22; annual
intensive-use window ~2026-09-10.

This is the living roadmap. Feature behavior lives in
[SPECIFICATION.md](SPECIFICATION.md), endpoints in [api.yaml](api.yaml),
deployment/ops in [DEPLOYMENT.md](DEPLOYMENT.md) /
[BACKUP_SETUP.md](BACKUP_SETUP.md), and implementation history in git
(PR titles carry the summaries this file used to duplicate).

---

## Current Snapshot

- **Tests:** 419 SQLite / 422 Postgres passing. The suite runs against
  Postgres by setting `TEST_DATABASE_URL` (per-test databases).
- **Access model:** `super_admin` + `admin` only. Unknown Google
  sign-ins auto-register as `unrecognised` (no access, no session);
  suspended accounts get 403 at the callback. Promotion happens via
  `/admin/users`.
- **Ops:** nightly age-encrypted `pg_dump` backups to the super-admin's
  Google Drive (30-day retention); super-admin UI database restore at
  `/admin/database-restore` (verify-then-swap; production-validated
  2026-08-19 including the older-dump migration path). `RESTORE_ENABLED`
  kill switch.

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
- Groupings: lifecycle, personnel exclusions/overrides, date editing
- Deferments (super-admin CRUD; `Personnel.callup_status`)
- Admin UI: dashboard, users, audit log, groupings, nominal rolls,
  taggings, deferments, CSV upload, DB restore
- User-facing views — `/grouping`, `/attendance`, `/nominal-roll` —
  built, but admin-gated pending the viewer role (below)

---

## Prioritized Open Work

### 1. Viewer role — before test users (weekend of 2026-08-22)

Open `/grouping`, `/attendance`, `/nominal-roll` to an appropriate
non-admin role. Deferred from Issue 12: routes and views already exist;
the work is the role decision (new `viewer` status vs reusing
`unrecognised`→promoted flow), route gating, nav, and attendance
permissions (subunit-1 scoping already exists).

### 2. UI test automation, Tier 1 — before the 2026-09-10 window

Behavioral page tests via the existing `client` fixture (GET page →
assert HTML → POST API → assert state) in `tests/behavioral/`. Covers
most of the manual-test burden that PRs #3/#4 exposed. Tier 2
(Playwright, `@pytest.mark.e2e`) once flows settle; Tier 3 (visual
regression) indefinitely deferred.

### 3. CSV Step 2: column mapping — before the window *if* the fixture changes

The process endpoint uses the fixed canonical map from the WY2627 ICT
fixture (`parade_state.utils.csv_constants`). Generalizing to arbitrary
fixtures becomes urgent the moment a cycle's NR export differs — check
the September fixture early. Step 3 (diff confirmation vs the active
NR) follows, and forces the file-reference decision below.

### 4. Mobile optimization (Phase 9E) — for field use during the window

Responsive design for tablets/phones. Original phase plan applies.

### 5. Reporting & analytics (Phase 7) — after the window produces data

Exception/summary reporting needs real usage patterns; revisit with
production data afterwards.

### 6. Performance & scalability (Phase 8) — as data grows

Indexing, query optimization, caching, background jobs. Post-window if
volumes justify it.

### 7. Deferments user-type scoping — minor

Deferment CRUD is super-admin-only with no user-type scoping; extend
when real deferment workflows emerge.

---

## Pending Decisions

**Nominal Roll file-reference convention (open since 2026-06-24).** NRs
reference their source file via `CsvUpload.original_filename` (joined).
Open questions: denormalize onto `NominalRoll`? filename vs
content-hash (`sha256_hash` exists) vs opaque upload ID? naming?
Defer until CSV Step 3 (diff confirmation) forces it.

---

## Recent History (one line each; git log is authoritative)

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
