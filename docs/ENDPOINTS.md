# API and Web Endpoint Reference

Quick-reference index of every route the app actually serves. Full
request/response shapes live in [api.yaml](api.yaml) (OpenAPI 3).

Roles: `super_admin` / `admin` / `user` (no `scoped` role). Below,
**auth'd** = any active session, **admin** = admin or super_admin,
**super** = super_admin.

## Feature Flags

Env kill switches gate whole routers — flag-off means **404** (JSON
`{"detail": "This feature is not available on this deployment
(FEATURE_X=false)"}` for `/api/*` paths, styled HTML 404 for pages) for
every role including super-admins:

| Flag | Gates |
|---|---|
| `FEATURE_ATTENDANCE` | `/attendance` page, `/api/v1/attendance/*` |
| `FEATURE_NOMINALROLL` | `/nominal-roll` page, `/admin/csv-upload`, `/admin/taggings`, `/api/v1/nominal-rolls/*`, `/api/v1/taggings/*`, `/api/v1/csv/*` |
| `FEATURE_GROUPING` | `/grouping` page, `/api/v1/groupings/*` |
| `FEATURE_DEFERMENTS` | `/admin/deferments` page, `/api/v1/deferments/*` |
| `FEATURE_DISCUSSIONS` | `/admin/discussions*` pages, `/api/v1/discussions/*` |
| `FEATURE_STRENGTH` | `/admin` (Unit Strength) page |

The issue-37 feature-access matrix (saved via
`POST /api/v1/admin/feature-access`) additionally **403**s the `admin`
role off attendance / nominal-roll / grouping surfaces toggled off in
Settings — super_admins bypass. (`/api/v1/personnel*` carries the matrix
gate only, no env flag.) `/docs`, `/redoc` and `/openapi.json`
exist only outside production.

## REST API Routes (`/api/v1`)

### Auth (JSON)
- `GET  /api/v1/auth/me` — auth'd — current user info
- `POST /api/v1/auth/logout` — auth'd — invalidate session token

### Users
- `GET    /api/v1/users/` — admin — list/filter users (`skip`, `limit`, `search`, `status_filter`, `role_filter`; `{users, total_count, skip, limit}`)
- `POST   /api/v1/users/` — super — pre-register a user (preregistered accounts sign in without the unrecognised step); 409 on duplicate email
- `GET    /api/v1/users/{id}` — auth'd — self for users, anyone for admins
- `PATCH  /api/v1/users/{id}` — admin — name/status/role/access_level_id; any change to a super_admin account or `role→super_admin` is super-only
- `DELETE /api/v1/users/{id}` — super — delete (no self-delete)

### Sessions (removed)
- `ANY /api/v1/sessions/*` — 410 Gone signpost; attendance is single-session on the active NR (see `/api/v1/attendance/`)

### Attendance (`FEATURE_ATTENDANCE`)
Statuses `present|absent`; optional reason `mc|off|early_outpro|other|awol`.
- `GET    /api/v1/attendance/` — admin — rows for `nominal_roll_id` + `date` (scope-filtered; missing rows render as Absent)
- `PUT    /api/v1/attendance/upsert` — admin — bulk upsert keyed (personnel_id, date); attendance-active NR only
- `POST   /api/v1/attendance/copy-remarks` — admin — query `nominal_roll_id`, `source_date`, `dest_date`, optional `sub_unit_1`
- `PUT    /api/v1/attendance/freeze` — super — freeze an (NR, date); 409 if already frozen
- `DELETE /api/v1/attendance/freeze` — super — unfreeze (query params); 404 if not frozen
- `GET    /api/v1/attendance/export` — admin — CSV of the marking table

### Personnel (`nominal_roll` matrix)
- `GET   /api/v1/personnel` — admin — filters (`nominal_roll_id`, unit/sub-units, `status`, `category`, `search`) + `sort_by`/`sort_order`, `limit`/`offset`; scope-filtered for admins
- `POST  /api/v1/personnel` — super — manual add (`source="manual"`, category inferred from rank)
- `GET   /api/v1/personnel/{id}` — admin — detail (effective values)
- `PATCH /api/v1/personnel/{id}` — admin — `rank`/`name` → 409 (NR read-only); `pers_no`, `inpro_status`, `unit`/`sub_unit_1` super-only; admins keep `sub_unit_2`/`sub_unit_3` (tagging overlay), `status`, `remarks`
- `GET   /api/v1/personnel/{id}/attendance-history` — admin — per-day rows + stats; `date_from`/`date_to`, `limit`/`offset`

### Nominal Rolls (`FEATURE_NOMINALROLL`)
- `GET    /api/v1/nominal-rolls` — admin — summaries; admins only NRs they hold grants on
- `GET    /api/v1/nominal-rolls/{id}` — admin — detail
- `PATCH  /api/v1/nominal-rolls/{id}` — admin — notes/label/remarks (label unique, 409)
- `DELETE /api/v1/nominal-rolls/{id}` — super — cascade delete; 400 while groupings reference the roll
- `POST   /api/v1/nominal-rolls/{id}/activate-attendance` — super — the one NR attendance writes may target (auto-switch)
- `POST   /api/v1/nominal-rolls/{id}/deactivate-attendance` — super
- `GET    /api/v1/nominal-rolls/{id}/export` — admin — CSV; filters `search`, `unit`, `sub_unit_1`, `sub_unit_2`, `category`, `rank`, `inpro_status`; no row cap

### Taggings (`FEATURE_NOMINALROLL`, super only)
1:1 with an NR — person → subunit remap overlay, never mutating the NR.
- `GET    /api/v1/taggings` — list (`nominal_roll_id`, `limit`, `offset`)
- `POST   /api/v1/taggings` — backfill-create; 409 if the NR already has one
- `GET    /api/v1/taggings/{id}` — detail with entries
- `PATCH  /api/v1/taggings/{id}` — label/remarks; `entries` full-replaces
- `DELETE /api/v1/taggings/{id}` — 409 if the NR has attendance rows
- `POST   /api/v1/taggings/{id}/clone` — merge entries into a target NR's tagging by pers_no

### Deferments (`FEATURE_DEFERMENTS`, super only)
- `GET    /api/v1/deferments` — filters `personnel_id`, `nominal_roll_id`, `status`, `reason`
- `POST   /api/v1/deferments` — create (starts `Pending action`)
- `GET    /api/v1/deferments/{id}`
- `PATCH  /api/v1/deferments/{id}` — status transitions drive `personnel.inpro_status` (leaving `Approved` reverts to `yet_to_inpro`)
- `DELETE /api/v1/deferments/{id}` — same revert rule

### Discussions (`FEATURE_DISCUSSIONS`)
- `GET    /api/v1/discussions/posts` — admin — newest first, cap 200 (`category`, `status_filter`)
- `POST   /api/v1/discussions/posts` — admin — create (starts `Open`)
- `GET    /api/v1/discussions/posts/{id}` — admin — detail + comments
- `PATCH  /api/v1/discussions/posts/{id}` — admin — author only (title/body)
- `DELETE /api/v1/discussions/posts/{id}` — super
- `PATCH  /api/v1/discussions/posts/{id}/triage` — super — category/status; audited
- `POST   /api/v1/discussions/posts/{id}/comments` — admin
- `PATCH  /api/v1/discussions/comments/{id}` — admin — author only
- `DELETE /api/v1/discussions/comments/{id}` — super

### Access Control (scope grants)
`*` wildcards a column; `(unit='*', sub_unit_1='*')` forbidden.
- `POST   /api/v1/access-control/nominal-rolls/{nr}/users/{user}/subunit-assignments` — super — grant (409 on duplicate)
- `GET    /api/v1/access-control/nominal-rolls/{nr}/subunit-assignments` — auth'd — grants on an NR (non-supers see only their own)
- `GET    /api/v1/access-control/nominal-rolls/{nr}/scope-options` — super — `{units, subunits_by_unit}` for the grant form
- `GET    /api/v1/access-control/users/{user}/subunit-assignments` — auth'd — self, or anyone for supers
- `DELETE /api/v1/access-control/nominal-rolls/{nr}/users/{user}/subunit-assignments/{id}` — super — returns 200 `{"detail": ...}` (not 204)

### CSV Ingestion (`FEATURE_NOMINALROLL`) — 2-step pipeline
1. `POST /api/v1/csv/upload` — super — multipart `file` + `auto_process` query; .csv only, 10 MB cap (413); duplicate sha256 returns the stored upload with `is_duplicate: true`
2. `POST /api/v1/csv/{upload_id}/process` — super — CAA date parsed from the filename (`caaYYMMDD` token); creates NR + personnel + auto-empty tagging; optional `source_nominal_roll_id` imports tagging entries by pers_no; 409 on re-process or existing CAA
- `GET /api/v1/csv/uploads` — admin — recent uploads (metadata only)

The old mapping/diff/confirm pipeline, column mappings and column
sensitivity endpoints are gone; there is no `caaDate` form field.

### Groupings (`FEATURE_GROUPING`)
Reads open to every role; all mutations super-only. Groupings live on the
attendance-active NR (unreachable 404 otherwise) and never touch attendance.
- `GET    /api/v1/groupings/` — auth'd — list
- `POST   /api/v1/groupings/` — super — create
- `GET    /api/v1/groupings/{id}` — auth'd — detail
- `PATCH  /api/v1/groupings/{id}` — super — label + full group-enum set; flags immutable (400)
- `DELETE /api/v1/groupings/{id}` — super — 204
- `PUT    /api/v1/groupings/{id}/personnel/{pid}/groups` — super — set membership
- `PATCH  /api/v1/groupings/{id}/personnel/{pid}/state` — super — checkbox/remarks
- `POST   /api/v1/groupings/{id}/clone` — super — same-NR clone (optional memberships/state)
- `POST   /api/v1/groupings/copy-from-previous` — super — from the previously activated NR (memberships re-link by pers_no)
- `GET    /api/v1/groupings/{id}/export` — auth'd — CSV

### Audit
- `GET /api/v1/audit/logs` — admin — filters `entity_type`, `action`, `target_user_id`; `limit`/`offset`; `{items, total, limit, offset}`

### Admin JSON
- `POST /api/v1/admin/feature-access` — super — save the admin-role feature-access matrix (`{items: [{feature_key, enabled}]}`)
- `POST /api/v1/admin/purge` — super — testing-only purge; `confirmation` query must equal `PURGE`; gated by `PURGE_ENABLED`
- `POST /api/v1/admin/database/restore` — super — pg_dump `.dump` upload (25 MB cap); `confirmation` = database name; gated by `RESTORE_ENABLED`, PostgreSQL only

## Web Pages (HTML, not API)

Session-cookie authenticated; unauthenticated browsers are redirected to
`/auth/login`. Admin-tier pages show an in-page no-access message for
plain admins where noted.

### Auth flow
- `GET /` — 302 → `/auth/login`
- `GET /auth/login` — login page; signed-in admins → `/admin`, other signed-ins → `/auth/no-access`
- `GET /auth/oauth/start` — begins the Google OAuth redirect
- `GET /auth/callback` — OAuth callback: unknown sign-ins auto-register as `unrecognised` (no session); only active admins get a session cookie and land on `/admin`; others get the no-access page (403)
- `GET /auth/logout` — clear cookie, back to login
- `GET /auth/no-access` — authenticated-but-forbidden page

There is **no** `/api/v1/auth/login` — the only way in is the OAuth flow,
and every API identity is derived from the session (issue 31).

### User-facing views
- `GET /attendance` — attendance marking table (admin tier; `FEATURE_ATTENDANCE`); filters NR / date / sub-unit / inpro / status / reason; frozen days read-only for non-supers
- `GET /nominal-roll` — NR browser (admin tier; `FEATURE_NOMINALROLL`); search + cascading filters, roll management panel (label/remarks, attendance toggle, delete)
- `GET /grouping` — grouping browser (any authenticated user; `FEATURE_GROUPING`); mutations render for supers only and are enforced server-side

### Admin pages
- `GET /admin` — Unit Strength report (`FEATURE_STRENGTH`)
- `GET /admin/users` — users management + scope-grant editing
- `GET /admin/csv-upload` — Upload NR (`FEATURE_NOMINALROLL`; hidden from plain admins by the `upload_nr` matrix toggle)
- `GET /admin/taggings` — tagging overlay editor (super; `FEATURE_NOMINALROLL`)
- `GET /admin/deferments` — deferments management (super; `FEATURE_DEFERMENTS`)
- `GET /admin/discussions` — board list (admin; `FEATURE_DISCUSSIONS`)
- `GET /admin/discussions/posts/{post_id}` — post detail with triage controls (admin page; triage API is super-only)
- `GET /admin/settings` — feature-access matrix (super)
- `GET /admin/audit` — audit log (filters + pagination)
- `GET /admin/database-restore` — Restore Backup (super)

### Utilities
- `GET /health` — `{status, version}`
- `GET /docs`, `GET /redoc`, `GET /openapi.json` — development only

## Sidebar / Pages Notes

The sidebar lists the workflow pages flat — Upload NR, Nominal Roll,
Taggings, Deferments, Attendance, Unit Strength, Grouping, Discussions —
then an **Admin** section (Users, Settings, Audit Log, Restore Backup).
Entries gate on feature flags (nav not rendered while off) and, for
admins, on the feature-access matrix. Retired pages: `/admin/nominal-rolls`,
`/admin/groupings`, `/admin/groupings/{id}/personnel`, `/admin/sessions`
(management moved into the user-facing views; the Dashboard was replaced
by Unit Strength).

## Authentication Semantics

- Session token: HttpOnly cookie set at OAuth sign-in (browser) or the
  same token as `Authorization: Bearer` (API clients); validated against
  the DB on every request; role re-read per request.
- `require_authenticated_user` → 401 without a valid session, 403 when
  the account is not `active`; `require_admin_user` adds the
  admin/super_admin check; `require_super_admin_user` the super_admin
  check.
- Scope grants (`UserSubunitAssignment`, unit + sub_unit_1 with `*`
  wildcards) further limit data reads/writes per NR to the effective
  location under the tagging overlay — deny-by-default, supers bypass.

## File Organization

- `src/parade_state/main.py` — app factory, router registration, feature-flag 404 handler
- `src/parade_state/api/*.py` — REST routers (68 route decorators, plus the sessions 410 stub)
- `src/parade_state/web/*.py` — user-facing views; `src/parade_state/admin_routes.py` — admin pages
- `src/parade_state/auth/` — OAuth, sessions, role dependencies
- `src/parade_state/models/schemas.py` — Pydantic request/response models
