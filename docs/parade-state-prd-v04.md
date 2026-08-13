# PRD: Battalion Parade State Management System

**Version:** 0.4 (draft)  
**Status:** For review  
**Last updated:** 2026-07-02  
**Changelog v0.4:** Tech stack pinned (FastAPI + NiceGUI + SQLAlchemy + APScheduler + Railway); deployment section updated for Railway; APScheduler job store model specified; mobile attendance UI confirmed as static HTML/JS for MVP; Vue SFC refactor deferred; Flask removed.  
**Amended 2026-07-02:** §8 Session — session creation restricted to `active` deployments (was: draft or active); session status lifecycle rewritten to reflect individual close/reopen (was: cascade-only via deployment). §9.1 — `excused` added to status enum; closed/finalized session read-only rule noted. See [SPECIFICATION.md](SPECIFICATION.md) for the authoritative current behavior.

> ⚠️ **SUPERSEDED — personnel identity sections.** This is a historical document.
> References to `pers_no` as an imported/used identifier are **obsolete**. `pers_no` is no
> longer imported or stored (it is an opaque, sensitive external primary key and is dropped on
> parse). Personnel are now identified by a server-generated 8-char base62 `short_id` that is
> stable across nominal rolls; cross-roll matching uses `full_name` (rank disambiguates). See
> [SPECIFICATION.md §3.2.1](SPECIFICATION.md) for the current model.

---

## 1. Problem Statement

The personnel branch currently manages battalion parade state through a manual mapreduce process — aggregating attendance from subunits by hand. This consumes most of a working day during parade state periods. This system replaces that process with a structured, access-controlled, deployment-aware web application suitable for field use.

---

## 2. Entity Hierarchy

```
Nominal Roll (CAA-pinned, CSV-sourced, immutable)
 └── Deployment (remaps personnel unit+subunit; has a date+time validity range)
      └── Session (AM or PM, admin-opened, linked to a deployment)
           └── Attendance Record (per-personnel per-session; notes write-back to deployment)
```

- **Nominal Roll** is the base source of truth. Uploaded from CSV, pinned by CAA date. Immutable after confirmation. Identified by content hash in addition to CAA for integrity verification.
- **Deployment** is based on a specific nominal roll. Remaps any subset of personnel to different unit+subunit assignments. Valid for a specified date+time range. Only one deployment is active at any point in time.
- **Session** is an AM or PM attendance window, explicitly opened by an admin, associated with a specific deployment. May be created in advance.
- **Attendance Record** is one record per personnel per session. Carries status, remarks (session-scoped), a notes snapshot (deployment-scoped, written at session open and each attendance write), and a unit+subunit snapshot (deployment assignment at time of write, subject to the validity-range rule).

---

## 3. Scope

**In scope (v1):**
- CSV ingestion with CAA versioning, column mapping, diff detection
- Required-column config in app config; column mapping table (global, admin-editable)
- Deployment management: create, clone (same-roll), migrate (cross-roll), scheduled activation
- Session management: admin-opens, advance creation, notes auto-snapshot on open
- Attendance taking: AM/PM, present/absent, Notes (deployment-scoped), Remarks (session-scoped)
- Row access control (access level + subunit scope) and column sensitivity control
- Parade state table view scoped to user access; inline editing
- Admin UI: enums, users, column sensitivity, column mapping, deployment/session management (NiceGUI)
- Mobile-friendly static HTML/JS attendance frontend
- Service worker + IndexedDB read-only cache (24hr TTL, stale indicator)
- SSE stale-detection signal on attendance view

**Out of scope (deferred):**
- Serviceman self-service access
- View projections / aggregated dashboards / export
- Automated push notifications or HQ reporting
- Vue SFC refactor of mobile frontend (revisit after MVP)

---

## 4. Users and Roles

### 4.1 Super-Admin
Bootstrapped via `SUPER_ADMIN_EMAIL` env var. Auto-granted on first Google sign-in. Cannot be revoked via UI.

### 4.2 App Admin
Granted by super-admin. Full read/write access to all entities, all columns, all deployments. Access to audit log. All structural operations (CSV upload, deployment management, session creation, clone/migrate, column mapping, user management) are admin-only.

### 4.3 Scoped User
Google-authenticated. Has:
- **Access level:** single admin-assigned label from the ordered access level vocabulary. Determines row visibility (which personnel they see) and column visibility (which columns they see).
- **Subunit scope:** one or more (deployment, subunit) pairs. A user sees personnel rows that fall within their scoped subunit(s) for a given deployment.
- **Write scope:** attendance status, Notes, Remarks — for rows within their scope only.

### 4.4 Account Lifecycle
1. Admin preregisters account by email with access level, subunit scope, and deployment grants assigned upfront. Account created in `pending` state.
2. On first Google sign-in, if email matches a `pending` account → activated. If no match → held as `unrecognised` (no access); auth event written to audit log.
3. Admin may suspend at any time. Suspension immediately invalidates active sessions.

---

## 5. Column Mapping

### 5.1 Global Mapping Table
Each entry: raw CSV column name → canonical app name (one-to-one per canonical), with status (`auto_detected` | `admin_confirmed` | `deprecated`), provenance timestamps, and editor identity. Admin-editable at any time. Applies to future uploads only — not retroactive. Entries are soft-deleted (deprecated), never hard-deleted.

**Constraint:** Each canonical app column may map to at most one raw CSV column. Multiple raw CSV columns may exist without a canonical mapping (stored in `extra_fields`).

### 5.2 Mapping on Upload
On CSV upload:
1. Auto-match CSV headers against global mapping table (case-insensitive, whitespace-normalised).
2. Any match that conflicts with an existing mapping is surfaced to admin for explicit confirmation.
3. Unmapped required columns block import until manually assigned.
4. Confirmed mappings update the global table (`admin_confirmed`).

### 5.3 Required Columns (App Config)
Declared in `app.config.json` (deployment-time change, not admin UI):

| Canonical name | Purpose |
|---|---|
| `unit` | Top-level unit identifier |
| `sub_unit_1` | Subunit level 1 |
| `sub_unit_2` | Subunit level 2 |
| `sub_unit_3` | Subunit level 3 |
| `pers_no` | Unique personnel identifier (cross-version matching key) |
| `rank` | Display |
| `full_name` | Display |

---

## 6. Nominal Roll (CSV Ingestion)

### 6.1 Storage
- Raw CSV stored immutably in `csv_uploads` (append-only; SHA-256 hash recorded).
- Parsed personnel in `personnel_snapshots`: required columns as typed fields; all others in `extra_fields JSONB`. **Each personnel record has an internal auto-generated `personnel_id` (UUID or serial); `pers_no` is stored in `extra_fields` as a read-only reference, never used for application logic or identity matching.**
- Column registry in `column_metadata` per CSV version: original name, canonical name, inferred type, sensitivity label.
- **Note on pers_no:** across CSV versions, internal `personnel_id` is *not* automatically transferred on pers_no match. `pers_no` is external-source data; notes and records belong to specific `personnel_id` entities within a deployment.

### 6.2 Upload Pipeline (3 steps)
1. **Upload:** file received, raw CSV stored, headers parsed, auto-matching attempted. Returns mapping resolution payload.
2. **Mapping confirmation:** admin resolves unmapped required columns and any conflicts. Global mapping table updated. **CAA conflict check:** if a CSV with the same CAA already exists in confirmed state, prompt admin for replacement confirmation. On confirm, prior confirmed CSV and its nominal roll are marked archived (soft-deleted). Diff computed against prior confirmed CSV (if different).
3. **Diff confirmation:** admin reviews single-page diff (max ~400 rows, sticky summary bar: N joined / N left / N changed). On confirm: personnel_snapshots populated, nominal roll deployment created, leavers archived, notes transferred from prior deployment by internal personnel ID (not `pers_no`).

---

## 7. Deployment

### 7.1 Data Model
Each deployment: name, nominal roll reference, status (`draft` | `active` | `inactive` | `archived` | `closed` | `finalized`), validity range (`valid_from` + `valid_until` datetimes), optional `scheduled_activation` datetime, personnel assignment overrides, and per-user access list.

**Deployment status lifecycle:** `draft` → `active` (auto or manual) → `inactive` (auto) → `archived` (optional admin action). Admins may also manually transition to `closed` (no further edits permitted) or `finalized` (permanent archive with all associated sessions finalized). A `finalized` deployment and all its sessions are immutable.

### 7.2 Lifecycle Rules
- Only one deployment `active` at any time (enforced by DB partial unique index + application layer).
- Validity range overlaps with any existing `draft` or `active` deployment are hard-rejected.
- Background job handles activation (at `valid_from` or `scheduled_activation`) and deactivation (at `valid_until`). Both transitions are idempotent.
- No write-lock on personnel overrides — editable at any deployment status for operational flexibility. Attendance snapshot integrity maintained by the validity-range rule (§9.2).

### 7.3 Clone (Same-Nominal Roll)
Admin-only. Copies overrides, prefixes name "Copy of …", resets validity range to blank. Admin chooses whether to transfer deployment notes.

### 7.4 Migrate (Cross-Nominal Roll)
Admin-only. Two-step: compute diff between source nominal roll and target nominal roll → present leavers (must be individually dismissed) and joiners (must each receive a unit+subunit assignment) → on confirm, create new draft deployment against target nominal roll.

---

## 8. Session

Admin-explicitly opened. Defined by deployment + date + session type (AM/PM). **At most one session per (deployment, date) pair across both AM and PM.** May be created only for **active** deployments (draft deployments must be activated first; inactive/archived/closed/finalized are rejected). On creation: all active personnel (minus deployment exclusions) pre-populated as `absent` with `notes_snapshot` from current `deployment_notes`. No retroactive session creation on inactive deployments.

**Session status lifecycle:** individual sessions move through `open → closed → finalized`, with admins able to transition each session independently. `closed → open` (reopen) is allowed and clears `closed_at`/`closed_by`. `finalized` is terminal. Deployment-level closure/finalization still cascades to child sessions, but individual session-level close/reopen is also supported. See [SPECIFICATION.md §4.3](SPECIFICATION.md) for the authoritative state machine and editability rules.

---

## 9. Attendance

### 9.1 Record
Fields: `personnel_id`, `session_id`, `deployment_id`, `status` (`present` | `absent` | `excused`), `remarks` (session-scoped), `notes_snapshot`, four unit+subunit snapshot fields, `updated_at`, `updated_by`. Records can only be created/updated/deleted while the linked session is `open`; closed and finalized sessions are read-only (HTTP 400).

### 9.2 Unit+Subunit Snapshot Rule
On any attendance write:
- **Within deployment validity range:** resolve effective assignment (override ?? nominal roll), write to all four `*_snapshot` fields.
- **Outside validity range** (retroactive edit by any scoped user with write privileges): update `status`, `remarks`, `notes_snapshot` only. Snapshot fields are not touched. Detailed audit trail (e.g., showing which override was active at time of write) deferred to future work.

### 9.3 Notes
Canonical store: `deployment_notes (deployment_id, pers_no)`. Editable wherever visible — in deployment view (writes to canonical only) or attendance session view (writes to canonical and updates `notes_snapshot` on current session's record). Transferred to new deployment by `pers_no` on new CSV confirmation. Leavers' notes remain in archived deployment only.

---

## 10. Access Control

### 10.1 Access Level Vocabulary
Admin-defined ordered string labels (e.g. `unit`, `coy`, `platoon`, `section`). Linear hierarchy (total ordering; higher `level_order` integer = broader access). Used for both row visibility and column sensitivity. Relabelling auto-migrates all references.

**Access level stability:** A user's access level is determined at login and remains stable for the duration of the session. Changes to a user's access level require re-login to take effect.

### 10.2 Row Visibility
User sees a personnel row if: subunit assignment falls within at least one subunit scope grant for the requested deployment AND user has a deployment grant for that deployment. Admins bypass both checks.

### 10.3 Column Visibility
Column visible if user's access level `level_order` ≥ column's sensitivity label `level_order`. `null` sensitivity label = admin-only. Applies uniformly across all visible rows. Admins see all columns.

### 10.4 Write Scope

| Role | Writable |
|---|---|
| Admin | All mutable fields |
| Scoped user | `status`, `notes`, `remarks` for rows within scope only |

---

## 11. Admin UI (NiceGUI)

Served at `/admin`. Built with NiceGUI mounted on the FastAPI app. Intended for desktop/tablet use by admins. Covers:
- CSV upload pipeline (3-step flow with mapping and diff review)
- Deployment management (create, edit, clone, migrate, activate)
- Session management (open, close)
- User management (preregister, edit access, grants)
- Column sensitivity configuration
- Column mapping table editor
- Enum management (unit/subunit values, access levels)
- App settings
- Audit log viewer

NiceGUI's WebSocket model is suitable for admin UI concurrency (few concurrent admin users, infrequent writes).

---

## 12. Mobile Attendance UI (Static HTML/JS)

Served at `/` (root). Single static HTML file with vanilla JS, served by FastAPI as a static file. Calls the REST API (`/api/v1/*`) for all data operations.

**MVP:** Vanilla JS, no build step, no framework dependency.  
**Post-MVP refactor target:** Vue 3 SFC (single-file components, CDN import, no Node.js build pipeline). Refactor is non-breaking — same API contract, same static file serving, different frontend implementation.

### 12.1 Features
- Session selector (date + AM/PM; defaults to admin-configured default session type)
- Deployment switcher (for users with multiple grants)
- Parade state table: columns from manifest only, rows scoped to user
- Inline status toggle (present/absent), remarks and notes inline edit
- Subunit filter, column sort
- SSE connection for stale detection: shows Refresh prompt on `data_changed` event

### 12.2 Offline / Cache
- Service worker registers on load
- On successful GET `/sessions/{id}/attendance`, caches response in IndexedDB with 24hr TTL
- If server unreachable, serves cached response with stale indicator in UI
- Cache is read-only; no offline writes

---

## 13. API

OpenAPI 3.1 spec maintained separately (`api.yaml`). Key design decisions:

- **Column manifest pattern (Option C):** all data endpoints return `columns` (user-visible column manifest) + `rows` (objects containing only manifest keys). Clients render headers from manifest; never hardcode column names.
- **SSE stale detection:** `GET /api/v1/events/attendance?deploymentId=&sessionId=` emits `data_changed` signal events (no payload data) when any record in the user's scope is modified. Client fetches on user confirmation. 30s keep-alive ping. Server closes connection on session expiry or access revocation.
- **Auth:** session cookie (HttpOnly, Secure, SameSite=Strict). Google OAuth via Authlib.

---

## 14. Non-Functional Requirements

| Concern | Requirement |
|---|---|
| Scale | Up to 200 concurrent users; read-heavy during parade state windows |
| Mobile | Responsive layout optimised for phone-width viewports; attendance operable one-handed |
| Performance | Table load < 2s on 4G; attendance write < 500ms round-trip |
| Client cache | Service worker + IndexedDB; read-only; 24hr max TTL; stale indicator |
| Auth | Google OAuth 2.0 via Authlib; HttpOnly session cookies |
| Accounts | Admin-preregistered; activated on first sign-in |
| Audit log | All writes appended with user + timestamp; admin-only access |
| Data retention | All entities retained indefinitely; no automatic purge |
| Secrets | All via Railway environment variables; never in source or `app.config.json` |

---

## 15. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | |
| API framework | FastAPI | Async; OpenAPI generation; SSE via `StreamingResponse` |
| Admin UI | NiceGUI | Mounted on FastAPI app at `/admin`; Quasar components |
| Mobile UI (MVP) | Static HTML + vanilla JS | Served by FastAPI; no build step |
| Mobile UI (post-MVP) | Vue 3 SFC via CDN | Drop-in replacement; same API contract |
| ORM | SQLAlchemy 2.x async | `asyncpg` driver; shared pool across FastAPI and NiceGUI |
| Auth | Authlib | Google OAuth 2.0; session middleware |
| Background jobs | APScheduler `AsyncIOScheduler` | SQLAlchemy job store (Postgres) for multi-instance safety |
| Database | PostgreSQL 15+ | Railway managed Postgres |
| Hosting | Railway | Single service; managed Postgres add-on |
| Package management | `uv` | Fast resolver; `pyproject.toml` |
| Process | Single uvicorn process | NiceGUI + FastAPI + APScheduler in one process |

### 15.1 Application Architecture

```
uvicorn
 └── FastAPI app
      ├── Authlib session middleware
      ├── /api/v1/*        REST API routes (attendance, deployments, sessions, etc.)
      ├── /admin/*         NiceGUI admin UI (mounted via nicegui.app.mount)
      ├── /                Static file serving (mobile HTML/JS)
      ├── /events/*        SSE endpoints
      └── APScheduler      Embedded async scheduler (deployment activation jobs)
```

SQLAlchemy async session factory shared across all layers. No separate worker process for MVP.

### 15.2 Railway Deployment

**Services:** one Railway service (Python app) + Railway managed Postgres add-on. No Redis, no separate worker service for MVP.

**Environment variables set in Railway dashboard:**

```
DATABASE_URL           # Injected automatically by Railway Postgres add-on
SUPER_ADMIN_EMAIL
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
SESSION_SECRET
APP_BASE_URL           # https://{your-app}.railway.app
```

**Start command:**
```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Railway injects `$PORT` automatically.

**Deploy flow:**
1. Push to main branch → Railway detects Python app via `pyproject.toml`
2. Installs dependencies via `uv`
3. Runs DB migrations (`alembic upgrade head`) as a Railway start command pre-step or via a one-off job
4. Starts uvicorn

**Multi-instance note:** Railway free/hobby tier runs a single instance. If scaled to multiple instances, APScheduler's SQLAlchemy job store (pointing at the shared Postgres) ensures deployment activation jobs execute exactly once across all instances. No code change required to scale.

**Persistent storage:** Railway volumes are available if needed for temporary file staging (e.g. CSV upload before parse). For MVP, uploaded CSV is read into memory and stored directly to the database — no filesystem persistence required.

---

## 16. Mobile UI Offline Changes

### 16.1 Offline Unsaved State Indicator
When a user has unsaved changes (remarks, notes, status) in a form field while offline or with network disruption, the affected field(s) are highlighted with visual emphasis:
- Font weight: bold
- Border/outline: orange or accent color

This alerts the user that the data is pending sync. On reconnect and successful push, highlight is removed.

---

## 17. Deployment Access Grants

When a new deployment is created, **all existing, active users automatically gain read+write access** to the new deployment within their existing subunit scopes. Admins retain full access. Explicit per-user grant assignment is not required for MVP.

---

## 18. Resolved Clarifications (v0.4)

1. **CAA conflict handling:** ✅ New upload with same CAA prompts admin to replace; prior CSV/nominal-roll archived.
2. **Retroactive edits:** ✅ Any scoped user with write privileges may edit attendance/remarks retroactively (outside validity range). Audit detail deferred.
3. **pers_no handling:** ✅ External reference ID, stored in `extra_fields`. Internal `personnel_id` (UUID) is the system identity. Notes/records follow `personnel_id`, not `pers_no`.
4. **Session constraints:** ✅ Max one session per (deployment, date); deployment closure cascades to all sessions; no session-level overrides.
5. **Column mapping:** ✅ Many-to-one (multiple CSV → one canonical allowed); each canonical maps to at most one CSV column.
6. **Access level stability:** ✅ Stable per session; access level changes require re-login.
7. **Deployment activation edge cases:** ⏳ Deferred pending implementation clarity.
8. **Session expiry & SSE:** ✅ Expiry = user logout; SSE forcibly closed on user suspension/removal.
9. **Audit log:** ✅ Sequential append of all changes; queryability pattern TBD.
10. **Offline UX:** ✅ Unsaved changes highlighted (bold + orange border).
11. **Deployment access on creation:** ✅ Auto-grant all existing users; explicit grants not required.
12. **Concurrent admin operations:** ⏳ TBD pending design review.

---

*End of PRD v0.4 (Updated 2026-05-07)*
