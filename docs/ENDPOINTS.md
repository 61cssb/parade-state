# API and Web Endpoint Documentation

This document clarifies the different types of endpoints in the Parade State application.

> **Feature flags:** the Deferments and Grouping features (web pages and
> `/api/v1/deferments/*`, `/api/v1/groupings/*`) are gated by the
> `FEATURE_DEFERMENTS` / `FEATURE_GROUPING` env vars — flag-off means 404
> for every role including super-admins, and their UI entry points are not
> rendered. On in development; off in production. See
> [DEPLOYMENT.md](DEPLOYMENT.md) › Feature Flags.

## Endpoint Types

### 1. Web Views (HTML Responses)
**Purpose:** User-facing pages that return HTML for browser rendering

**Authentication Routes:**
- `GET /auth/login` - Login page with "Sign in with Google" button
- `GET /auth/oauth/start` - Initiates Google OAuth flow (redirects to Google)
- `GET /auth/callback` - OAuth callback handler (creates session, redirects to admin)
- `GET /auth/logout` - Logout handler (clears cookies, redirects to login)

**User-Facing View Routes (Phase 9D / 9F):**
- `GET /grouping` - Grouping summary (today's AM/PM session counts, unit breakdown) — grouping selector dropdown plus an expandable grouping-management panel (metadata, Edit Dates, lifecycle transitions, Delete, Manage Personnel link — merged from the retired admin groupings page)
- `GET /grouping/{id}/personnel` - Grouping personnel management (checkbox-based include/exclude, draft-only editing)
- `GET /attendance` - Attendance marking table (inline status/remarks editing) — grouping + session selector
- `GET /nominal-roll` - Nominal Roll browser (row-numbered roster table with unit/sub-unit columns, search, unit filter) — nominal roll selector dropdown plus an expandable roll-management panel (label/remarks editing, Create Grouping, attendance toggle, Delete — merged from the retired admin nominal rolls page)

**Admin Interface Routes:**
- `GET /admin` - Admin dashboard
- `GET /admin/users` - Users management page
- `GET /admin/csv-upload` - Upload NR page (CSV upload with automatic processing into Nominal Rolls)
- `GET /admin/settings` - Settings page
- `GET /admin/audit` - Audit log page
- `GET /admin/taggings` - Tagging overlay management (super-admin only; plain admins get an in-page no-access message)
- `GET /admin/deferments` - Deferments management (super-admin only; in-page no-access message for plain admins)
- `GET /admin/database-restore` - Restore Backup page (super-admin only; in-page no-access message for plain admins)

**Note:** The sidebar lists the workflow pages flat (Dashboard, Upload NR, Nominal Roll, Taggings, Deferments, Attendance, Grouping), then an **Admin** section (Users, Settings, Audit Log, Restore Backup). The former `/admin/nominal-rolls`, `/admin/groupings`, `/admin/groupings/{id}/personnel`, and `/admin/sessions` pages were retired when their management moved into the user-facing views. Sessions (AM/PM) are hardcoded; the REST APIs `/api/v1/groupings/*` and `/api/v1/sessions/*` remain separate.

**Characteristics:**
- Return HTML responses (Jinja2 templates)
- Handle browser redirects (HTTP 302)
- Use cookie-based authentication
- **NOT** part of REST API
- Intended for browser navigation, not API clients

### 2. REST API Endpoints (JSON Responses)
**Purpose:** JSON API for programmatic access

**Authentication API:**
- `POST /api/v1/auth/login` - Login with email/password (if implemented)
- `POST /api/v1/auth/logout` - API logout
- `GET /api/v1/auth/me` - Get current user info

**Users API:**
- `GET /api/v1/users/` - List users
- `GET /api/v1/users/{id}` - Get user details
- `PATCH /api/v1/users/{id}` - Update user (creates AuditLog entry)
- `DELETE /api/v1/users/{id}` - Delete user

**Other APIs:**
- `/api/v1/groupings/*` - Grouping management (CRUD, lifecycle transitions, personnel exclusions, overrides, notes, status export)
- `/api/v1/sessions/*` - Session management
- `/api/v1/attendance/*` - Attendance records
- `/api/v1/personnel/*` - Personnel management
- `/api/v1/access-control/*` - Access control
- `/api/v1/csv/*` - CSV upload and ingestion
- `/api/v1/nominal-rolls/*` - Nominal Roll list/detail (admin-only), confirm/unconfirm (PATCH), delete (super_admin, DELETE)
- `/api/v1/audit/*` - Audit log

### 3. CSV Upload API

**Purpose:** CSV file ingestion for establishment/roster data

**Endpoints:**
- `POST /api/v1/csv/upload` - Upload CSV file (returns upload ID, SHA256 hash, detected columns, line count, duplicate flag)
- `GET /api/v1/csv/uploads` - List previous uploads (paginated, metadata only)

**Status:** Step 1 (file ingestion) implemented. Models in [csv_ingestion.py](src/parade_state/models/csv_ingestion.py).

**3-Step Pipeline:**
1. **Upload** (implemented) — File ingestion, SHA256 hashing, column detection, duplicate detection
2. **Mapping** (deferred) — Map raw columns to canonical names
3. **Diff** (deferred) — Compare against current active establishment

**Characteristics:**
- Return JSON responses
- Use token-based authentication (Bearer token in Authorization header)
- Part of OpenAPI documentation (`/docs`, `/redoc`)
- Intended for API clients (mobile apps, SPAs, scripts)

### 4. Audit Log API

**Purpose:** Read-only access to system audit trail (user management actions, CSV uploads, future entity changes)

**Endpoints:**
- `GET /api/v1/audit/logs` - List audit entries with filtering (entity_type, action, target_user_id) and pagination

**Auth pattern:** Query params (`user_id`, `user_role`) — consistent with CSV upload API. Requires admin or super_admin role.

**Response:** Paginated list with `user_name` and `user_email` resolved via left outer join on User. System-generated entries (null `user_id`) return `user_name: null`.

**Entity types:** attendance, grouping, session, user, csv_upload, nominal roll, personnel, access_level, column_mapping

**Actions:** create, update, delete, archive, close, finalize

### 5. Health & Utility Endpoints
- `GET /health` - Health check endpoint (returns JSON)
- `GET /docs` - Swagger UI (OpenAPI documentation)
- `GET /redoc` - ReDoc documentation

## Authentication Flow

### Web UI Authentication (Browser)
```
1. Browser: GET /auth/login
2. Server: Returns login page (HTML)
3. User: Clicks "Sign in with Google"
4. Browser: GET /auth/oauth/start  
5. Server: Redirects to Google OAuth
6. User: Completes Google OAuth
7. Google: Redirects to /auth/callback?code=xxx
8. Server: Creates session, sets httponly cookie, redirects to /admin (admins) or /grouping (regular users)
9. Browser: Accesses page with cookie
10. Server: Validates cookie, returns appropriate view (HTML)
```

### API Authentication (Programmatic)
```
1. Client: POST /api/v1/auth/login (or use OAuth)
2. Server: Returns session token
3. Client: Stores token
4. Client: GET /api/v1/users/ -H "Authorization: Bearer {token}"
5. Server: Validates token, returns user data (JSON)
```

## Route Registration

In `main.py`:
```python
# Web routes (HTML responses)
app.include_router(web_auth_router, prefix="/auth", tags=["web-auth"])
app.include_router(web_grouping_router, tags=["web-grouping"])
app.include_router(web_attendance_router, tags=["web-attendance"])
app.include_router(admin_router, tags=["admin"])

# API routes (JSON responses)  
app.include_router(auth.router, prefix="/api/v1/auth", tags=["api-auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
# ... other API routers
```

## Important Notes

1. **Cookie vs Token Authentication:**
   - Web UI uses httponly cookies (secure, prevents XSS)
   - API uses Bearer tokens (standard for programmatic access)

2. **No Conflict:** 
   - Web routes (`/auth/*`, `/admin/*`) return HTML
   - API routes (`/api/v1/*`) return JSON
   - Different paths, different purposes

3. **Security:**
   - Web cookies are httponly (JavaScript cannot access)
   - API tokens should be stored securely by clients
   - Both use the same session database backend

4. **Documentation:**
   - API endpoints: Auto-documented at `/docs` (Swagger UI)
   - Web endpoints: Not in OpenAPI (they return HTML, not JSON)

## File Organization

**Web Routes:**
- `src/parade_state/web/auth.py` - Authentication web views
- `src/parade_state/web/grouping.py` - Grouping summary view (non-admin)
- `src/parade_state/web/attendance.py` - Attendance marking view (non-admin)
- `src/parade_state/web/nominal-roll.py` - Nominal Roll browser view (non-admin)
- `src/parade_state/admin_routes.py` - Admin interface routes
- `src/parade_state/templates/` - Jinja2 templates

**API Routes:**
- `src/parade_state/api/auth.py` - Authentication API
- `src/parade_state/api/users.py` - Users API
- `src/parade_state/api/nominal_rolls.py` - Nominal Roll list/detail API
- `src/parade_state/api/*.py` - Other API endpoints

**Authentication Logic:**
- `src/parade_state/auth/oauth.py` - OAuth client setup
- `src/parade_state/auth/session.py` - Session creation/validation
- `src/parade_state/auth/admin_dependencies.py` - Admin route protection