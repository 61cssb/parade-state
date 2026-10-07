# System Architecture

**Version:** 1.1  
**Date:** 2026-10-07  
**Status:** Architecture Overview  

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Architecture](#2-component-architecture)
3. [Module Architecture](#3-module-architecture)
4. [Data Flow](#4-data-flow)
5. [Entity Relationships](#5-entity-relationships)
6. [Technology Stack](#6-technology-stack)
7. [Deployment Architecture](#7-deployment-architecture)
8. [Security Architecture](#8-security-architecture)
9. [Code Standards & Best Practices](#9-code-standards--best-practices)
10. [Testing Strategy](#10-testing-strategy)
11. [Performance & Scalability](#11-performance--scalability)

---

## 1. System Overview

### 1.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Parade State System                       │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Web pages   │  │  Admin UI    │  │   REST API   │     │
│  │  (Jinja2)    │  │  (Jinja2)    │  │  (FastAPI)   │     │
│  │  /attendance │  │  /admin/*    │  │  /api/v1/*   │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│         │                  │                  │              │
│         └──────────────────┴──────────────────┘              │
│                            │                                  │
│                   ┌───────────────────────┐                  │
│                   │  Application Layer   │                  │
│                   │  (Business Logic)    │                  │
│                   └───────────────────────┘                  │
│                            │                                  │
│                   ┌───────────────────────┐                  │
│                   │    Data Access Layer  │                  │
│                   │   (SQLAlchemy ORM)    │                  │
│                   └───────────────────────┘                  │
│                            │                                  │
│                   ┌───────────────────────┐                  │
│                   │   Database Layer      │                  │
│                   │  (PostgreSQL/SQLite)  │                  │
│                   └───────────────────────┘                  │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

All three surfaces are served by the same FastAPI process: server-rendered
Jinja2 pages for the user-facing views and the admin section, and a JSON
REST API under `/api/v1/*`. There is no separate SPA build step and no
static frontend bundle.

### 1.2 Application Architecture

```
uvicorn (single process)
 └── FastAPI app
      ├── Middleware: CORSMiddleware, SessionMiddleware (OAuth flow
      │   state), FeatureAccessMiddleware (issue 37 matrix)
      ├── /auth/*          Login page, OAuth start/callback, logout, no-access
      ├── /attendance      Attendance marking view (Jinja2)
      ├── /nominal-roll    NR roster browser (Jinja2)
      ├── /grouping        Grouping browser (Jinja2, flag-gated)
      ├── /admin/*         Admin pages (Jinja2, via admin_routes.py)
      ├── /api/v1/*        REST API routes (JSON; sessions/* = 410 Gone stub)
      ├── /                Redirects to /auth/login
      └── /health          Health check
```

**Key design decisions:**
- Single uvicorn process for MVP (no separate worker)
- SQLAlchemy async session factory shared across all layers
- No Redis and no background job queue — all work happens in-request
- Stateless API design for horizontal scaling

---

## 2. Component Architecture

### 2.1 Frontend Components

All "frontend" is server-rendered Jinja2 templates (`src/parade_state/templates/`)
— there is no JavaScript build step, no SPA, and no service worker.

#### User-Facing Web Pages (Jinja2)
- **Technology:** Jinja2 templates (shared `base.html` shell)
- **Served at:** `/attendance` (marking), `/nominal-roll` (roster browser), `/grouping` (flag-gated viewer)
- **Purpose:** Field attendance taking and roster browsing
- **Features:**
  - Responsive server-rendered pages (no offline mode — a network connection is assumed)
  - Per-row autosave on the attendance page (fetch calls to the REST API)
  - View filters (sub-unit, Inpro Status, Status/Reason) applied server-side and client-side
  - `/` redirects to `/auth/login`, which routes active admins to `/admin`

#### Admin UI (Jinja2 templates)
- **Technology:** Jinja2 templates (shared `base.html` shell)
- **Served at:** `/admin` (Unit Strength report), `/admin/*` pages
- **Purpose:** System administration + strength reporting
- **Features:**
  - Unit Strength report (`/admin`, `FEATURE_STRENGTH`-gated)
  - CSV upload pipeline (`/admin/csv-upload`)
  - User management + subunit scope grants (`/admin/users`)
  - Taggings manager (`/admin/taggings`)
  - Deferments (`/admin/deferments`)
  - Discussions board (`/admin/discussions`)
  - Audit log viewer (`/admin/audit`)
  - Settings: feature-access matrix + DB restore (`/admin/settings`, `/admin/database-restore`)

### 2.2 Backend Components

#### REST API (FastAPI)
- **Purpose:** Data operations and business logic
- **Authentication:** Google OAuth; DB-backed session token carried by the
  HttpOnly auth cookie (admin UI) or a Bearer header (API clients)
- **Key endpoints:**
  - `/api/v1/attendance/*` - Attendance operations (list, upsert, copy-remarks, freeze, export)
  - `/api/v1/nominal-rolls/*` - Nominal Roll lifecycle + attendance activation + CSV export
  - `/api/v1/personnel/*` - Personnel listing, manual add, remaps, attendance history
  - `/api/v1/groupings/*` - Grouping management (flag-gated)
  - `/api/v1/taggings/*` - Tagging overlay management
  - `/api/v1/users/*` - User operations
  - `/api/v1/sessions/*` - 410 Gone signposts (sessions were removed)

#### Background Scheduler
- **None.** There is no scheduler and no job queue. The scheduled
  grouping-activation jobs died with the old groupings design (issue 26
  redesign: no lifecycle, no validity windows, no scheduled activation).
  Attendance is gated by the active-NR switch
  (`NominalRoll.attendance_active`), not by time-based jobs.

---

## 3. Module Architecture

### 3.1 Module Dependency Tree

The `parade_state` application follows a strict layered architecture with clear dependency boundaries. Modules are organized into 5 layers, where each layer only depends on lower layers.

```
┌─────────────────────────────────────────────────────────────┐
│                    LAYER 5: Application                      │
│                    parade_state.main                        │
│                    (FastAPI app orchestration)              │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ depends on
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    LAYER 4: Routes                          │
│                                                              │
│  ┌────────────────────┐    ┌────────────────────┐          │
│  │ parade_state.web   │    │ parade_state.api   │          │
│  │ + admin_routes     │    │ (REST API)         │          │
│  │ (Jinja2 pages,     │    │ /api/v1/*          │          │
│  │  OAuth flows)      │    │ (JSON responses)   │          │
│  │ /auth/* /attendance│    │                    │          │
│  │ /nominal-roll      │    │                    │          │
│  │ /grouping /admin/* │    │                    │          │
│  └────────────────────┘    └────────────────────┘          │
│           │                           │                     │
│           └───────────┬───────────────┘                     │
│                       ▼                                     │
│         ┌───────────────────────────┐                      │
│         │ parade_state.auth         │                      │
│         │ (dependencies, session,   │                      │
│         │  oauth, admin deps)       │                      │
│         └───────────────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ depends on
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   LAYER 3: Business Logic                   │
│                   parade_state.config                        │
│                   parade_state.features                      │
│                   parade_state.feature_access                │
│                   (Configuration, flags, access matrix)      │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ depends on
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    LAYER 2: Data Models                     │
│                    parade_state.models/*                     │
│                    (Database models & schemas)               │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ depends on
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   LAYER 1: Foundation                        │
│                   parade_state.utils/*                       │
│                   parade_state.db                            │
│                   (Core utilities & database)                │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Layer Breakdown

#### **Layer 1: Foundation (No internal dependencies)**

**`parade_state.utils`**
- **Modules:** `cookies.py`, `csv_constants.py`, `env.py`, `ids.py`, `markdown.py`, `ranks.py`, `utc_dt.py`
- **Purpose:** Core utility functions with no internal dependencies
- **Dependencies:** Standard library only
- **Initialization:** First
- **Key responsibilities:**
  - Environment variable access (`env`)
  - UUID generation and validation (`ids`)
  - UTC datetime operations (`utc_dt`)
  - Cookie set/get/clear with consistent security flags (`cookies`)
  - CSV ingestion contract: header-name column matching (`csv_constants`)
  - Safe markdown rendering for discussions (`markdown`)
  - Rank-to-category mapping (`ranks`)

**`parade_state.db`**
- **Modules:** `__init__.py`, `restore.py`
- **Purpose:** Database connection and session management
- **Dependencies:** `parade_state.utils.ids`
- **Initialization:** Second
- **Key responsibilities:**
  - SQLAlchemy engine and session factory (`init_database`, `get_db_session`)
  - `normalize_database_url` (platform `postgresql://` URLs → `postgresql+asyncpg://`, `sslmode=` → `ssl=`)
  - Base model class with String(36) UUID default via `ids.db_default`
  - Verified in-app restore from a `pg_dump` archive (`restore.py`: restore into a
    temp database, verify, swap, conditionally `alembic upgrade head`)

#### **Layer 2: Data Models**

**`parade_state.models`**
- **Modules:** `access.py`, `attendance.py`, `audit.py`, `auth_session.py`, `csv_ingestion.py`, `deferments.py`, `discussions.py`, `grouping.py`, `personnel.py`, `schemas.py`, `tagging.py`
- **Purpose:** Database models and Pydantic schemas
- **Dependencies:** `parade_state.db` (for `Base` class)
- **Initialization:** Third
- **Key responsibilities:**
  - SQLAlchemy ORM models
  - Database schema definition
  - Request/response validation schemas
- **Note:** Uses `TYPE_CHECKING` to avoid circular dependencies

#### **Layer 3: Business Logic**

**`parade_state.config`**
- **Purpose:** Application configuration management (`Settings` read from env, production validation)
- **Dependencies:** `parade_state.utils.env`
- **Initialization:** Fourth

**`parade_state.features`**
- **Purpose:** Env-var feature flags (`require_feature` dependency; flag-off = 404 for every role)

**`parade_state.feature_access`**
- **Purpose:** Per-role feature-access matrix (issue 37): `FeatureAccessMiddleware`,
  page-route gate, `require_feature_access(key)` API dependency; fails open

#### **Layer 4: Routes & Authentication**

**`parade_state.auth`**
- **Modules:** `admin_dependencies.py`, `dependencies.py`, `oauth.py`, `session.py`
- **Purpose:** Authentication and authorization utilities
- **Dependencies:** `parade_state.models`, `parade_state.db`, `parade_state.utils`
- **Initialization:** Fifth
- **Note:** Reusable authentication logic for both API and web routes. Bearer
  header or HttpOnly auth cookie both resolve to the same DB-backed
  `UserSession` token.

**`parade_state.web`**
- **Modules:** `auth.py`, `attendance.py`, `grouping.py`, `nominal_roll.py`
- **Purpose:** User-facing web routes (OAuth flows, Jinja2 page views)
- **Dependencies:** `parade_state.auth`, `parade_state.models`, `parade_state.db`
- **Initialization:** Sixth
- **Note:** Returns HTML/redirects, not JSON

**`parade_state.admin_routes`** (top-level module)
- **Purpose:** Admin section routes (`/admin/*`), Jinja2-rendered
- **Note:** Unit Strength report, users, CSV upload, taggings, deferments,
  discussions, audit, settings, DB restore

**`parade_state.api`**
- **Modules:** `access_control.py`, `admin_purge.py`, `attendance.py`, `audit.py`, `auth.py`, `csv_upload.py`, `db_restore.py`, `deferments.py`, `discussions.py`, `feature_access.py`, `groupings.py`, `nominal_rolls.py`, `personnel.py`, `sessions.py`, `subunit_access.py`, `tagging.py`, `users.py`
- **Purpose:** REST API route handlers (JSON responses)
- **Dependencies:** All previous layers
- **Initialization:** Seventh
- **Note:** Pure JSON API, documented in OpenAPI. `subunit_access.py` is the
  shared enforcement seam for NR-scoped row-level access (issue #28/#31);
  `sessions.py` is the 410-Gone catch-all.

#### **Layer 5: Application**

**`parade_state.main`**
- **Purpose:** FastAPI application setup and lifecycle management
- **Dependencies:** All API modules, `parade_state.db`, `parade_state.utils`
- **Initialization:** Last (orchestrates all modules)

### 3.3 Initialization Order

**Recommended startup sequence:**

1. **Foundation** → Load utilities and database configuration
2. **Database** → Initialize database engine and session factory
3. **Models** → Import and register ORM models
4. **Business Logic** → Load configuration, auth, and session management
5. **API Layer** → Initialize middleware and route handlers
6. **Application** → Create FastAPI app and mount routes

**Current implementation** ([`main.py:69-81`](../src/parade_state/main.py#L69-L81)):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    # Startup
    # Only initialize if not already initialized (prevents test
    # database from being reset)
    from parade_state.db import get_session_maker

    if get_session_maker() is None:
        init_database(settings.DATABASE_URL)
    yield
    # Shutdown
    pass
```

`settings.DATABASE_URL` comes from the environment (`Settings` in
`config.py`); when unset it defaults to in-memory SQLite
(`sqlite+aiosqlite:///:memory:`). Local development normally loads `.env`,
which points at a file-based SQLite database (`parade_state.db`); production
injects the Railway PostgreSQL URL. Schema creation is not part of the
lifespan: the container runs `alembic upgrade head` before uvicorn starts
(see section 7).

### 3.4 Circular Dependency Management

**✅ No Runtime Circular Dependencies**

The codebase successfully avoids circular dependencies through three key patterns:

#### **1. TYPE_CHECKING Pattern**

Models use forward references to break import cycles:

```python
# models/access.py
from typing import TYPE_CHECKING
from ..db import Base

if TYPE_CHECKING:
    from .auth_session import UserSession
    from .csv_ingestion import ColumnMetadata, NominalRoll
```

**Why this works:**
- `TYPE_CHECKING` is `False` at runtime, preventing circular imports
- Type checkers and IDEs still see the full type information
- Relationships work via string references (e.g., `"Grouping"`)

#### **2. Dependency Injection**

FastAPI's dependency injection breaks circular dependency chains:

```python
# API endpoints receive dependencies via FastAPI DI
async def endpoint(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_authenticated_user)
):
    # No direct imports needed at module level
```

**Benefits:**
- Modules don't need to import each other directly
- Dependencies are resolved at runtime, not import time
- Easier testing with mock dependencies

#### **3. Clear Layer Boundaries**

Each layer only depends on lower layers:

```
Application → API → Business Logic → Models → Foundation
```

**This prevents:**
- Upward dependencies (lower layers importing higher layers)
- Peer dependencies (modules at same level importing each other)
- Cross-cutting concerns (modules importing across layers)

### 3.5 Dependency Risks & Mitigations

#### **Model Relationships (Low Risk ✅)**

**Risk:** Models reference each other via relationships
**Example:** `User` → `UserSession` → `User` (via `back_populates`)

**Mitigation:**
- Use `TYPE_CHECKING` for type hints
- Use string references for relationships
- No runtime imports between models

#### **API ↔ Session ↔ Models (Low Risk ✅)**

**Risk:** API modules import session, which imports models

**Mitigation:**
- Clear one-way dependency flow
- Session module doesn't import API modules
- Models don't import API modules

#### **Middleware ↔ Models (Low Risk ✅)**

**Risk:** Middleware (`FeatureAccessMiddleware`) reads model-backed data
(the feature-access matrix) on every page request

**Mitigation:**
- One tiny SELECT per page request, fails open on any error
- Result stashed on `request.state` for the render; no model mutation
- Regular request-scoped database operations still go through
  dependency injection

### 3.6 Web Routes vs REST API

The application separates user-facing web routes from REST API endpoints for clear architectural boundaries.

#### **Web Routes (`parade_state.web`)**

**Purpose:** Handle browser-based authentication flows and user interactions

**Characteristics:**
- Return HTTP redirects or rendered HTML, not JSON
- Handle OAuth flows (Google OAuth)
- Intended for frontend navigation
- Not documented in OpenAPI/Swagger

**URL Structure:**
```
/auth/login        → Login page (redirects signed-in admins to /admin)
/auth/oauth/start  → Redirect to Google OAuth
/auth/callback     → OAuth callback: set HttpOnly auth cookie, redirect to /admin
/auth/logout       → Clear auth cookie, redirect to login
/auth/no-access    → 403 page for authenticated non-admins
```

**Example:**
```python
@router.get("/oauth/start")
async def start_oauth(request: Request):
    """Start the OAuth flow with Google."""
    base_url = f"{request.url.scheme}://{request.url.netloc}"
    redirect_uri = f"{base_url}/auth/callback"

    oauth = get_oauth()
    google = oauth.create_client("google")
    return await google.authorize_redirect(request, redirect_uri)
```

#### **REST API (`parade_state.api`)**

**Purpose:** Provide JSON API for frontend clients, mobile apps, and integrations

**Characteristics:**
- Return JSON responses only
- Accept a Bearer token header or the HttpOnly session cookie
- Documented in OpenAPI/Swagger (`/docs`, disabled in production)
- Intended for programmatic access

**URL Structure:**
```
/api/v1/auth/me     → Get current user info
/api/v1/auth/logout → Logout user
/api/v1/users/      → List users
/api/v1/attendance/ → Attendance marking table
/api/v1/groupings/  → Manage groupings
```

**Example:**
```python
@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(require_authenticated_user),
) -> dict:
    """Get current user information (REST API)."""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
    }
```

#### **Authentication Logic (`parade_state.auth`)**

**Purpose:** Reusable authentication utilities for both web and API

**Modules:**
- `dependencies.py` - FastAPI dependencies for authentication (Bearer-or-cookie)
- `admin_dependencies.py` - Optional admin/user resolution for page routes
- `session.py` - Session management utilities
- `oauth.py` - OAuth client configuration

**Benefits of Separation:**

1. **Clear API Contract:** REST API clients only see JSON endpoints
2. **Frontend Independence:** Can change OAuth flow without affecting API
3. **Testing:** Test API endpoints independently of OAuth
4. **Documentation:** OpenAPI docs only show relevant endpoints
5. **Deployment:** Can deploy web routes and API separately if needed

#### **Authentication Flow**

```
1. User visits /auth/login and clicks "Sign in with Google"
   ↓
2. /auth/oauth/start redirects to Google (Authlib OAuth client)
   ↓
3. Google OAuth flow completes
   ↓
4. Google redirects to /auth/callback (web route)
   ↓
5. Server upserts the User; only active admins (admin / super_admin)
   proceed — everyone else gets the 403 no-access page and no session
   ↓
6. Server creates a DB-backed UserSession (7-day token) and sets the
   HttpOnly auth cookie (24 hours, SameSite=lax)
   ↓
7. Browser is redirected to /admin; same-origin fetches send the cookie
   automatically (API clients may instead send the token as Bearer)
   ↓
8. API dependencies validate the token against the database per request
```

### 3.7 Dependency Rules

**When adding new modules:**

1. **Check layer placement** - Which layer does your module belong to?
2. **Verify dependencies** - Only import from lower layers
3. **Use TYPE_CHECKING** - For forward references in models
4. **Prefer dependency injection** - For runtime dependencies
5. **Test imports** - Ensure no circular import errors

**Example - Adding a new utility:**
```python
# ✅ CORRECT - New utility in foundation layer
# parade_state/utils/validation.py
from parade_state.utils import ids  # Same layer, OK

# ❌ WRONG - Importing from higher layer
from parade_state.models import User  # Violates layer boundaries
```

**Example - Adding a new API endpoint:**
```python
# ✅ CORRECT - Using dependency injection
@router.get("/endpoint")
async def endpoint(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_authenticated_user)
):
    # Use injected dependencies

# ❌ WRONG - Direct imports at module level
from parade_state.api.some_module import function  # Circular risk
```

---

## 4. Data Flow

### 4.1 Authentication Flow

```
┌─────────┐         ┌──────────┐         ┌──────────┐
│  User   │─────>  │  Google  │─────>  │ FastAPI  │
│ Browser │         │   OAuth  │         │  Auth    │
└─────────┘         └──────────┘         └──────────┘
     │                                        │
     │                                        │
     └────────<──────── Auth Cookie ──────────┘
```

**Process:**
1. User clicks "Sign in with Google" on `/auth/login`
2. `/auth/oauth/start` redirects to Google OAuth
3. Google redirects back with authorization code
4. FastAPI exchanges code for user info
5. Existing email → update sign-in timestamp; new email → auto-register
   (bootstrap super-admin active, everyone else `unrecognised`)
6. Suspended or non-admin accounts get the 403 no-access page, no session
7. Active admins: create `UserSession` (7-day token) and set the
   HttpOnly auth cookie (24 hours)
8. Redirect to `/admin`

### 4.2 CSV Upload Flow

```
┌──────────┐   Upload   ┌──────────┐   Parse    ┌──────────┐
│   User   │──────────>│ FastAPI  │──────────>│   CSV    │
│ Browser  │           │ Endpoint  │           │  Parser  │
└──────────┘           └──────────┘           └──────────┘
                                                        │
                                                        │
                                                        v
                                                 ┌──────────────┐
                                                 │   Column     │
                                                 │  Mapping     │
                                                 │  Resolution  │
                                                 └──────────────┘
                                                        │
                                                        v
                                                 ┌──────────────┐
                                                 │     Diff     │
                                                 │  Calculation │
                                                 └──────────────┘
                                                        │
                                                        v
                                                 ┌──────────────┐
                                                 │    Nominal Roll     │
                                                 │  Creation    │
                                                 └──────────────┘
```

### 4.3 Attendance Taking Flow

```
┌──────────┐  Request  ┌──────────┐  Query   ┌──────────┐
│   User   │─────────>│ FastAPI  │────────>│Database  │
│ Browser  │          │ Endpoint  │         │          │
└──────────┘          └──────────┘         └──────────┘
     │                      │                    │
     │                      │<────── Data ────────┘
     │                      │
     └────<── JSON Response ─┘
```

**Access control in flow:**
1. Auth cookie (or Bearer token) provides identity, validated per request
2. User's role and subunit scope determine visible rows (`api/subunit_access.py`)
3. Attendance writes validated against scope; frozen days are read-only for admins
4. Every write is against the one NR active for attendance

---

## 5. Entity Relationships

### 5.1 Core Entity Hierarchy

```
Nominal Roll (CSV source of truth)
 │
 ├── Personnel (roster entries)
 │    ├── Attendance (one row per personnel/day; active-NR gated)
 │    ├── GroupingMembership (group memberships within a grouping)
 │    └── GroupingMemberState (per-grouping checkbox + remarks)
 │
 ├── Grouping (labelled set of groups on the roll; issue 26 redesign)
 │    └── GroupingGroup (closed vocabulary; position = display order)
 │
 ├── Tagging (exactly one per NR; 1:1 subunit overlay)
 │    └── TaggingEntry (per-person from_* → to_* remap)
 │
 ├── Deferment (per-personnel deferment records)
 │
 └── CsvUpload (raw file storage)
```

Groupings never interact with attendance: no attendance columns,
endpoints, or exports in the grouping feature, and no grouping coupling
anywhere in the attendance path.

### 5.2 User Management Hierarchy

```
AccessLevel (vocabulary; orders ColumnMetadata sensitivity)
 │
 ├── User (Google-authenticated accounts; roles: super_admin / admin / user)
 │    ├── UserSession (DB-backed session tokens)
 │    └── UserSubunitAssignment (NR-scoped (unit, sub_unit_1) write grants)
 │
 └── FeatureAccess (per-role feature matrix; issue 37)
```

(The old grouping-specific scoping tables — GroupingUserAccess and
UserSubunitScope — were removed in the issue 26 redesign.)

### 5.3 Attendance Tracking Hierarchy

```
Nominal Roll (the one active for attendance; 1:1 Tagging overlay applied)
 │
 ├── Attendance (per-personnel per-day; UNIQUE(personnel_id, date))
 │    ├── status (present/absent) / reason (nullable enum) / remarks
 │    └── unit_snapshot / sub_unit_*_snapshot (frozen at write time)
 │
 └── AttendanceFreeze (row presence = (NR, date) frozen; issue 35)
```

Sessions and the AM/PM split were removed (issues #4/#33): there is no
`Session` model and no `session_id` anywhere; `/api/v1/sessions/*` returns
410 Gone.

### 5.4 Key Cascades

**Grouping cascades:**
- Grouping deleted → its groups, memberships, and member state cascade
- GroupingGroup deleted → that group's memberships cascade (servicemen
  become ungrouped; blocked under allow_ungrouped=false)
- Group label renamed in place → memberships reference the row, so every
  member follows

**Nominal Roll cascades:**
- Nominal Roll deleted → personnel, attendance, freezes, taggings cascade;
  deletion refused (400) while groupings still reference the roll
  (FK RESTRICT)

**User cascades:**
- User deleted → all scopes and access grants soft-deleted
- User suspended → active sessions immediately invalidated

---

## 6. Technology Stack

### 6.1 Technology Choices

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Frontend** | | |
| Web UI (user-facing) | Jinja2 server-rendered pages | No build step, no SPA; scoping and flags enforced server-side |
| Admin UI | Jinja2 templates | Same stack as the rest of the app; shared `base.html` shell |
| **Backend** | | |
| API Framework | FastAPI | Async support, auto OpenAPI docs, type validation |
| ORM | SQLAlchemy 2.x async | Mature, async support, cross-database compatibility |
| Auth | Authlib | Google OAuth 2.0 client (flows only) |
| Sessions | Starlette SessionMiddleware + DB `UserSession` tokens | Signed cookie carries OAuth flow state; API auth uses DB-backed tokens via HttpOnly cookie or Bearer header |
| Templates | Jinja2 + markdown2 | Server-rendered pages; safe markdown for discussions |
| **Database** | | |
| Production | PostgreSQL 18 (Railway) | ACID compliance, JSONB, partial indexes, proven reliability |
| Testing | SQLite (file-based, one per test) | Fast, isolated, cross-platform, async support |
| Migrations | Alembic (async env) | Versioned schema; container upgrades before serving |
| **Infrastructure** | | |
| Hosting | Railway | Simple deployment, managed Postgres, CI/CD |
| Package Manager | uv | Fast dependency resolution, lock files |
| Process | Single uvicorn | FastAPI + Jinja2 + Alembic in one process |

### 6.2 Async Architecture

**Why async throughout:**

```python
# FastAPI async endpoint (api/attendance.py, abridged)
@router.get("/", response_model=list[AttendanceResponse])
async def list_attendance(
    nominal_roll_id: str,
    attendance_date: date,
    current_user: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Attendance).where(
            Attendance.nominal_roll_id == nominal_roll_id,
            Attendance.date == attendance_date,
        )
    )
    return result.scalars().all()
```

**Benefits:**
- Non-blocking I/O operations
- Better concurrent request handling
- Efficient database connection usage
- Works seamlessly with FastAPI's async model

### 6.3 Database Compatibility

**Cross-database compatibility strategy:**

```python
# Works in both SQLite (testing) and PostgreSQL (production)
class Base(DeclarativeBase):
    id: Mapped[str] = mapped_column(
        String(36),              # String storage for SQLite compatibility
        primary_key=True,
        default=ids.db_default,  # parade_state.utils.ids
        index=True,
    )
```

**SQLite limitations handled:**
- UUIDs stored as String(36) instead of native UUID (same representation in
  production PostgreSQL — no native UUID columns anywhere)
- JSON instead of JSONB (automatic serialization)
- Uniqueness via `UniqueConstraint` (e.g. attendance `UNIQUE(personnel_id, date)`)
- Referential integrity via ORM/FKs, not DB triggers

---

## 7. Deployment Architecture

### 7.1 Single Instance Deployment (MVP)

```
┌──────────────────────────────────────┐
│        Railway Service (Docker)      │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │   uvicorn process               │ │
│  │                                 │ │
│  │  ┌─────────────────────────┐   │ │
│  │  │   FastAPI App           │   │ │
│  │  │   - REST API (/api/v1)  │   │ │
│  │  │   - Jinja2 pages        │   │ │
│  │  │   - Jinja2 admin UI     │   │ │
│  │  │   - Alembic (upgrade    │   │ │
│  │  │     head before serve)  │   │ │
│  │  └─────────────────────────┘   │ │
│  │                                 │ │
│  │  ┌─────────────────────────┐   │ │
│  │  │   SQLAlchemy async      │   │ │
│  │  │   (asyncpg pool)        │   │ │
│  │  └─────────────────────────┘   │ │
│  └─────────────────────────────────┘ │
│               │                      │
└───────────────┼──────────────────────┘
                │
                v
┌──────────────────────────────────────┐
│   Railway Managed PostgreSQL 18      │
│   (single application database)      │
└──────────────────────────────────────┘
```

The container (python:3.12-slim + uv, non-root user) installs
postgresql-client-18 so the admin-UI restore can run `pg_restore` against
the same-major server. The start command runs `alembic upgrade head && uvicorn
parade_state.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers`
(`--proxy-headers` honors Railway's X-Forwarded-Proto so OAuth redirect
URIs and Secure cookies see https).

### 7.2 Future Multi-Instance Architecture

```
┌─────────────────────┐    ┌─────────────────────┐
│  Railway Instance 1  │    │  Railway Instance 2  │
│                     │    │                     │
│  ┌───────────────┐  │    │  ┌───────────────┐  │
│  │   FastAPI     │  │    │  │   FastAPI     │  │
│  │   + Alembic   │  │    │  │   + Alembic   │  │
│  └───────────────┘  │    │  └───────────────┘  │
└─────────┬───────────┘    └─────────┬───────────┘
          │                          │
          └──────────┬───────────────┘
                     │
                     v
        ┌────────────────────────────────┐
        │   Shared PostgreSQL             │
        │   (single application database; │
        │   one instance owns migrations) │
        └────────────────────────────────┘
```

**No code changes required:**
- Session tokens are DB rows, so any instance can validate any request
- OAuth flow state lives in signed cookies, not process memory
- Stateless API design allows horizontal scaling
- Run migrations from one deploy target only to avoid concurrent upgrades

---

## 8. Security Architecture

### 8.1 Authentication & Authorization

```
┌─────────────────────────────────────────────────────────┐
│                   Security Layers                       │
│                                                           │
│  ┌───────────────────────────────────────────────────┐  │
│  │  1. Network Security                               │  │
│  │     - HTTPS/TLS encryption                         │  │
│  │     - Railway-managed certificates                 │  │
│  └───────────────────────────────────────────────────┘  │
│                         │                                 │
│  ┌───────────────────────────────────────────────────┐  │
│  │  2. Authentication                                │  │
│  │     - Google OAuth 2.0 (Authlib client)           │  │
│  │     - HttpOnly session cookies                    │  │
│  │     - SameSite=lax CSRF protection                │  │
│  └───────────────────────────────────────────────────┘  │
│                         │                                 │
│  ┌───────────────────────────────────────────────────┐  │
│  │  3. Authorization                                 │  │
│  │     - Role-based access control                   │  │
│  │     - Access level + subunit scoping              │  │
│  │     - Column-level sensitivity                    │  │
│  └───────────────────────────────────────────────────┘  │
│                         │                                 │
│  ┌───────────────────────────────────────────────────┐  │
│  │  4. Data Security                                 │  │
│  │     - Database encryption at rest (PostgreSQL)    │  │
│  │     - Row-level security via user scoping         │  │
│  │     - Audit trail for all changes                 │  │
│  └───────────────────────────────────────────────────┘  │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

### 8.2 Access Control Implementation

**Row-level security:**
```python
# User reads/writes personnel/attendance rows within their scope grants
# (issue #28: api/subunit_access.py is the single enforcement seam)
async def get_writable_personnel(user_id: str, user_role: str, nominal_roll_id: str):
    # (unit, sub_unit_1) grants for this roll; '*' = wildcard column
    grants = await get_scope_grants(db, user_id, nominal_roll_id)

    # Personnel whose effective (unit, sub_unit_1) — tagging overlay
    # applied — matches any grant; super_admin bypasses
    locations = await resolve_effective_locations(db, pids, tagging_id)
    return [
        p for p in personnel
        if grant_matches(grants, *locations[p.id])
    ]
```

**Column-level security:**
```python
# User sees columns where access_level >= column_sensitivity
async def get_visible_columns(user_id: str):
    user_access_level = await get_user_access_level(user_id)

    # Get columns where sensitivity_level <= user_access_level
    columns = await query_columns_by_sensitivity(user_access_level)
    return columns
```

### 8.3 Grouping Access Control (per-grouping scoping removed)

The multi-tenant, per-grouping access model (GroupingUserAccess grants
plus UserSubunitScope scoping) was **removed** in the issue 26 groupings
redesign — the redesigned groupings carry no access scoping at all:

- Grouping **mutations** are super-admin only (403 otherwise), enforced
  server-side on every API route
- Grouping **reads** (page and API) are open to every authenticated role
- Reachability is a property of the nominal roll: groupings on the roll
  active for attendance are listable/readable; groupings on non-active
  rolls are retained in the database but unreachable (404) until their
  roll is re-activated

Row-level write scoping elsewhere in the app is per nominal roll
(`UserSubunitAssignment` on effective `sub_unit_1`), not per grouping.

### 8.4 Session Management Implementation

Two cookie/session mechanisms coexist, each with a single job:

1. **OAuth flow state** — Starlette `SessionMiddleware` (cookie
   `session_data`, `max_age=86400`, `SameSite=lax`, signed with
   `SESSION_SECRET`). Exists only to carry the OAuth handshake state;
   Authlib is used purely as the OAuth client.
2. **Application authentication** — a DB-backed `UserSession` token stored
   in the HttpOnly auth cookie `session_token` (`utils/cookies.py`:
   HttpOnly, `SameSite=lax`, 24-hour expiry, Secure in production via
   `AUTH_COOKIE_SECURE`). API clients may send the same token as an
   `Authorization: Bearer` header instead; dependencies accept both.

**Session Storage Pattern:**
```python
class UserSession(Base):
    """User authentication session for managing login state and access control."""
    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    # ... email, name, role, created_at, expires_at, last_accessed_at,
    #     user_agent, ip_address
```

**Critical Pattern - UUID String Storage:**
- **Database Storage**: UUIDs stored as **strings** (`Mapped[str]`) for SQLite compatibility
- **Database Queries**: Use **string comparison** (not UUID objects)
- **Validation**: Convert to UUID objects only for format validation

```python
# ✅ CORRECT: String comparison for database queries
result = await db.execute(
    select(User).where(User.id == session.user_id)  # Both strings
)

# ❌ WRONG: UUID object comparison fails
result = await db.execute(
    select(User).where(User.id == ids.to_uuid(session.user_id))  # UUID vs string
)
```

**Authentication Flow:**
1. User logs in via Google OAuth → creates/updates `User` record
2. System creates `UserSession` with secure token → stores in database
3. Server sets the HttpOnly auth cookie (24 hours) and redirects to `/admin`
4. HTTP request presents the cookie (or a Bearer header) →
   `require_authenticated_user()` dependency
5. Dependency validates token via `get_valid_session()` → retrieves UserSession
6. Dependency looks up User by string ID → returns authenticated user
7. Request proceeds with user context

**Security Features:**
- **Token Generation**: `secrets.token_urlsafe(32)` for 256-bit security
- **Expiration**: 7-day DB session default; 24-hour auth cookie
- **Tracking**: Stores IP, user agent, last accessed time
- **Validation**: Every request validates session in database

---

## 9. Code Standards & Best Practices

### 9.1 Development Patterns

This project follows specific development patterns to ensure consistency and maintainability. For comprehensive development guidance, see **[CLAUDE.md](../CLAUDE.md)**.

**Key patterns used:**

**Utility Module Pattern:**
- Use centralized utility modules instead of native Python datatypes
- Example: `from parade_state.utils import utc_dt` for all datetime operations
- Ensures consistent timezone handling, database compatibility, and easier maintenance

**Async Database Operations:**
- Always use async database operations with FastAPI
- Use dependency injection for database sessions
- Never mix sync and async database operations

**Type Annotations:**
- Complete type annotations on all functions
- Enables better IDE support and catches type errors early
- Required for FastAPI request/response validation

**Explicit Error Handling:**
- Use specific HTTP status codes and descriptive error messages
- Clear API contract via OpenAPI documentation

For detailed development patterns and examples, refer to **[CLAUDE.md](../CLAUDE.md)**.

---

## 10. Testing Strategy

### 10.1 Testing Philosophy

The project follows a **testing pyramid** approach with clear separation of concerns:

```
                 /\
                /  \
               / E2E\           (Future: End-to-end UI tests)
              /------\
             /        \
            / Integration \    (API endpoints, database)
           /--------------\
          /                  \
         /     Unit Tests      \  (Functions, models, logic)
        /----------------------\
```

**Testing priorities:**
1. **Unit tests** - Fast, isolated tests of business logic
2. **Integration tests** - API endpoint testing with real database
3. **Behavioral tests** - Domain logic and system behavior validation

### 10.2 FastAPI Testing Approach

**Decision:** Use FastAPI's built-in **TestClient** instead of httpx.AsyncClient

**Rationale:**

| Aspect | FastAPI TestClient | httpx.AsyncClient |
|--------|-------------------|-------------------|
| **Interface** | Synchronous | Async (requires await) |
| **Dependencies** | Built into FastAPI | Additional dependency |
| **Performance** | Lower overhead | Higher overhead |
| **Framework Match** | Designed for FastAPI | Generic HTTP client |
| **Complexity** | Simpler test code | More complex test code |

**Implementation:**
```python
# ✅ CORRECT - Use TestClient synchronously
from fastapi.testclient import TestClient

def test_endpoint(client: TestClient):
    response = client.get("/api/v1/users")  # No await
    assert response.status_code == 200

# ❌ WRONG - Don't use httpx for basic testing
import httpx

async def test_endpoint():
    async with httpx.AsyncClient(app=app) as client:
        response = await client.get("/api/v1/users")  # Unnecessary complexity
```

**Benefits:**
1. **Simplicity** - No async/await complexity in test code
2. **Performance** - Lower overhead for our use case
3. **Maintainability** - Less complex, easier to understand
4. **Dependencies** - Fewer direct dependencies to manage

### 10.3 Test Organization

**Unit Tests (`tests/unit/`)**
- **Purpose:** Test isolated functions and modules
- **Characteristics:** Fast, no database/network, use mocks
- **Example:** Testing `utc_dt.now()` with various inputs

**Integration Tests (`tests/integration/`)**
- **Purpose:** Test API endpoints with database
- **Characteristics:** Real database (file-based SQLite, one file per test
  under `tmp_path`; optional PostgreSQL via `TEST_DATABASE_URL`), HTTP requests
- **Example:** Testing `POST /api/v1/attendance/upsert` with authentication

**Behavioral Tests (`tests/behavioral/`)**
- **Purpose:** Test domain logic and business rules
- **Characteristics:** Database models, constraints, system behavior
- **Example:** Testing access control hierarchy enforcement

### 10.4 Dependency Decisions

**httpx (removed 2026-05-09, restored since):**

**Decision history:** httpx was removed as a direct dependency on
2026-05-09 in favor of FastAPI's TestClient (sync, built in, less
overhead) — that conversion still holds: every integration test uses
`fastapi.testclient.TestClient`, not `httpx.AsyncClient`.

**Update:** httpx is a direct dependency again in `pyproject.toml`
(`httpx>=0.24.0`) because Authlib's OAuth client requires it at runtime.
It is still not used for testing.

**Current testing stack:**
- `fastapi.testclient.TestClient` for all HTTP-level tests
- `pytest`, `pytest-asyncio`, `pytest-cov` (dev dependency group)

### 10.5 Test Coverage Requirements

**Minimum Coverage: 60% (enforced)**

The coverage gate lives in `pyproject.toml`
(`--cov-fail-under=60` in `[tool.pytest.ini_options]`); the suite
currently sits above it (~62%).

**Verification:**
```bash
uv run pytest   # addopts already enable coverage + the gate
```

---

## 11. Performance & Scalability

### 11.1 Current Performance Characteristics

**Test results:**
- 685 tests (681 passing, 4 skipped) execute in ~2.5-3 minutes
- Test database: file-based SQLite, one file per test under `tmp_path`
  (optional PostgreSQL via `TEST_DATABASE_URL`)
- Coverage gate: 60% enforced in `pyproject.toml` (currently ~62%)

**Expected production performance:**
- API response time: < 200ms for typical queries
- Page load time: < 2s on 4G
- Attendance write: < 500ms round-trip

### 11.2 Scalability Considerations

**Current single-instance limits:**
- Max concurrent users: ~200 (based on typical FastAPI performance)
- Database connections: Managed by the asyncpg connection pool
- Background jobs: none — all work happens in-request

**Future scaling path:**
1. **Horizontal scaling:** Add more Railway instances (see 7.2)
2. **Database scaling:** PostgreSQL read replicas for heavy read operations
3. **Caching:** Redis cache for frequently accessed data (sessions, groupings)
4. **CDN:** Not applicable today — no static asset bundle; pages are server-rendered

---

*End of System Architecture v1.0*
