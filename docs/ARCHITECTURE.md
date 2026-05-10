# System Architecture

**Version:** 1.0  
**Date:** 2026-05-08  
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
│  │ Mobile UI    │  │  Admin UI    │  │   REST API   │     │
│  │ (Static HTML)│  │   (NiceGUI)  │  │   (FastAPI)  │     │
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

### 1.2 Application Architecture

```
uvicorn (single process)
 └── FastAPI app
      ├── Authlib session middleware
      ├── /api/v1/*        REST API routes (attendance, deployments, sessions, etc.)
      ├── /admin/*         NiceGUI admin UI (mounted via nicegui.app.mount)
      ├── /                Static file serving (mobile HTML/JS)
      ├── /events/*        SSE endpoints
      └── APScheduler      Embedded async scheduler (deployment activation jobs)
```

**Key design decisions:**
- Single uvicorn process for MVP (no separate worker)
- SQLAlchemy async session factory shared across all layers
- No Redis required for MVP (database for job storage)
- Stateless API design for horizontal scaling

---

## 2. Component Architecture

### 2.1 Frontend Components

#### Mobile UI (Static HTML/JS)
- **Technology:** Vanilla JavaScript, HTML5, CSS3
- **Served at:** `/` (root path)
- **Purpose:** Field attendance taking
- **Features:**
  - Responsive mobile-first design
  - Offline support via Service Worker + IndexedDB
  - SSE for real-time updates
  - Progressive Web App capabilities

#### Admin UI (NiceGUI)
- **Technology:** NiceGUI (Quasar components)
- **Served at:** `/admin`
- **Purpose:** System administration
- **Features:**
  - CSV upload pipeline
  - Deployment management
  - User management
  - Column configuration
  - Audit log viewer

### 2.2 Backend Components

#### REST API (FastAPI)
- **Purpose:** Data operations and business logic
- **Authentication:** Google OAuth + session cookies
- **Key endpoints:**
  - `/api/v1/attendance/*` - Attendance operations
  - `/api/v1/deployments/*` - Deployment management
  - `/api/v1/sessions/*` - Session management
  - `/api/v1/users/*` - User operations
  - `/api/v1/events/*` - SSE endpoints

#### Background Scheduler (APScheduler)
- **Purpose:** Time-based deployment activation/deactivation
- **Storage:** SQLAlchemy job store (PostgreSQL)
- **Jobs:**
  - Deployment activation at `valid_from` or `scheduled_activation`
  - Deployment deactivation at `valid_until`
  - Idempotent execution for safety

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
│  │ (OAuth flows)      │    │ (REST API)         │          │
│  │ /auth/login        │    │ /api/v1/*          │          │
│  │ /auth/callback     │    │ (JSON responses)   │          │
│  └────────────────────┘    └────────────────────┘          │
│           │                           │                     │
│           └───────────┬───────────────┘                     │
│                       ▼                                     │
│         ┌───────────────────────────┐                      │
│         │ parade_state.auth         │                      │
│         │ (dependencies, session)   │                      │
│         └───────────────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ depends on
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   LAYER 3: Business Logic                   │
│                   parade_state.config                        │
│                   (Configuration & domain logic)             │
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
- **Modules:** `env.py`, `ids.py`, `utc_dt.py`
- **Purpose:** Core utility functions with no internal dependencies
- **Dependencies:** Standard library only
- **Initialization:** First
- **Key responsibilities:**
  - Environment variable access (`env`)
  - UUID generation and validation (`ids`)
  - UTC datetime operations (`utc_dt`)

**`parade_state.db`**
- **Purpose:** Database connection and session management
- **Dependencies:** `parade_state.utils.ids`
- **Initialization:** Second
- **Key responsibilities:**
  - SQLAlchemy engine and session factory
  - Base model class with default UUID generation
  - Database initialization lifecycle

#### **Layer 2: Data Models**

**`parade_state.models`**
- **Modules:** `access.py`, `attendance.py`, `audit.py`, `auth_session.py`, `csv_ingestion.py`, `deployment.py`, `personnel.py`, `schemas.py`
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
- **Purpose:** Application configuration management
- **Dependencies:** `parade_state.utils.env`
- **Initialization:** Fourth

#### **Layer 4: Routes & Authentication**

**`parade_state.auth`**
- **Modules:** `dependencies.py`, `session.py`, `oauth.py`
- **Purpose:** Authentication and authorization utilities
- **Dependencies:** `parade_state.models`, `parade_state.db`, `parade_state.utils`
- **Initialization:** Fifth
- **Note:** Reusable authentication logic for both API and web routes

**`parade_state.web`**
- **Modules:** `auth.py`
- **Purpose:** User-facing web routes (OAuth flows, redirects)
- **Dependencies:** `parade_state.auth`, `parade_state.models`, `parade_state.db`
- **Initialization:** Sixth
- **Note:** Returns HTML/redirects, not JSON

**`parade_state.api`**
- **Modules:** `auth.py`, `users.py`, `deployments.py`, `sessions.py`, `attendance.py`, `personnel.py`, `access_control.py`
- **Purpose:** REST API route handlers (JSON responses)
- **Dependencies:** All previous layers
- **Initialization:** Seventh
- **Note:** Pure JSON API, documented in OpenAPI

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

**Current implementation** ([`main.py:22-29`](../src/parade_state/main.py#L22-L29)):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    database_url = env.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    init_database(database_url)  # Step 2
    yield
    # Shutdown
```

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
    from .deployment import Deployment
```

**Why this works:**
- `TYPE_CHECKING` is `False` at runtime, preventing circular imports
- Type checkers and IDEs still see the full type information
- Relationships work via string references (e.g., `"Deployment"`)

#### **2. Dependency Injection**

FastAPI's dependency injection breaks circular dependency chains:

```python
# API endpoints receive dependencies via FastAPI DI
async def endpoint(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
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

**Risk:** Middleware imports models for type annotations

**Mitigation:**
- Middleware only uses models for type hints
- No runtime model operations in middleware
- All database operations via dependency injection

### 3.6 Web Routes vs REST API

The application separates user-facing web routes from REST API endpoints for clear architectural boundaries.

#### **Web Routes (`parade_state.web`)**

**Purpose:** Handle browser-based authentication flows and user interactions

**Characteristics:**
- Return HTTP redirects, not JSON
- Handle OAuth flows (Google OAuth)
- Intended for frontend navigation
- Not documented in OpenAPI/Swagger

**URL Structure:**
```
/auth/login     → Redirect to Google OAuth
/auth/callback  → OAuth callback, redirect to frontend with token
```

**Example:**
```python
@router.get("/login")
async def login(request: Request):
    """Initiate Google OAuth login flow (user-facing)."""
    return await google.authorize_redirect(request, redirect_uri)
```

#### **REST API (`parade_state.api`)**

**Purpose:** Provide JSON API for frontend clients, mobile apps, and integrations

**Characteristics:**
- Return JSON responses only
- Require Bearer token authentication
- Documented in OpenAPI/Swagger (`/docs`)
- Intended for programmatic access

**URL Structure:**
```
/api/v1/auth/me     → Get current user info
/api/v1/auth/logout → Logout user
/api/v1/users/      → List users
/api/v1/deployments/ → Manage deployments
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
- `dependencies.py` - FastAPI dependencies for authentication
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
1. User clicks "Sign in with Google"
   ↓
2. Frontend redirects to /auth/login (web route)
   ↓
3. Google OAuth flow completes
   ↓
4. Google redirects to /auth/callback (web route)
   ↓
5. Server creates session and redirects to frontend with token
   ↓
6. Frontend stores token for API calls
   ↓
7. Frontend calls /api/v1/auth/me with Bearer token (API route)
   ↓
8. Server returns JSON user data
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
    current_user: User = Depends(get_current_user)
):
    # Use injected dependencies

# ❌ WRONG - Direct imports at module level
from parade_state.api.some_module import function  # Circular risk
```

---

## 3. Data Flow

### 3.1 Authentication Flow

```
┌─────────┐         ┌──────────┐         ┌──────────┐
│  User   │─────>  │  Google  │─────>  │ FastAPI  │
│ Browser │         │   OAuth  │         │  Auth    │
└─────────┘         └──────────┘         └──────────┘
     │                                        │
     │                                        │
     └────────<──────── Session Cookie ──────┘
```

**Process:**
1. User clicks "Sign in with Google"
2. Redirect to Google OAuth
3. Google redirects back with authorization code
4. FastAPI exchanges code for user info
5. Check if email matches preregistered user
6. Create session cookie
7. Redirect to application

### 3.2 CSV Upload Flow

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
                                                 │    Estab     │
                                                 │  Creation    │
                                                 └──────────────┘
```

### 3.3 Attendance Taking Flow

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
1. User session cookie provides identity
2. User's access level determines visible columns
3. User's subunit scope determines visible rows
4. Attendance writes validated against scope

---

## 5. Entity Relationships

### 4.1 Core Entity Hierarchy

```
Estab (CSV source of truth)
 │
 ├── Personnel (roster entries)
 │    ├── DeploymentPersonnelOverride (assignment changes)
 │    ├── DeploymentNotes (per-deployment notes)
 │    └── AttendanceRecord (session attendance)
 │
 ├── Deployment (operational windows)
 │    ├── DeploymentPersonnelOverride
 │    ├── DeploymentNotes
 │    ├── Session (AM/PM windows)
 │    │    └── AttendanceRecord
 │    ├── DeploymentUserAccess (user grants)
 │    └── UserSubunitScope (user scoping)
 │
 └── CsvUpload (raw file storage)
```

### 4.2 User Management Hierarchy

```
AccessLevel (vocabulary)
 │
 └── User (Google-authenticated accounts)
      ├── UserSubunitScope (deployment-specific scoping)
      └── DeploymentUserAccess (deployment grants)
```

### 4.3 Attendance Tracking Hierarchy

```
Deployment
 │
 └── Session (AM/PM windows)
      └── AttendanceRecord (per-personnel records)
           ├── status (present/absent)
           ├── remarks (session-scoped)
           ├── notes_snapshot (deployment notes frozen at session open)
           └── unit_snapshot (personnel assignment frozen at write time)
```

### 4.4 Key Cascades

**Deployment cascades:**
- Deployment deleted → all sessions, overrides, notes, access grants deleted
- Deployment closed → all sessions marked closed
- Deployment finalized → all sessions marked finalized

**Estab cascades:**
- Estab deleted → all personnel records deleted
- Estab confirmed → creates initial draft deployment

**User cascades:**
- User deleted → all scopes and access grants soft-deleted
- User suspended → active sessions immediately invalidated

---

## 6. Technology Stack

### 5.1 Technology Choices

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Frontend** | | |
| Mobile UI | Vanilla JavaScript + HTML | No build step, works offline, progressive web app |
| Admin UI | NiceGUI | Fast development, Quasar components, integrates with FastAPI |
| **Backend** | | |
| API Framework | FastAPI | Async support, auto OpenAPI docs, type validation |
| ORM | SQLAlchemy 2.x async | Mature, async support, cross-database compatibility |
| Auth | Authlib | Google OAuth 2.0, session management |
| Scheduler | APScheduler | Async scheduler, SQLAlchemy job store |
| **Database** | | |
| Production | PostgreSQL 15+ | ACID compliance, JSONB, partial indexes, proven reliability |
| Testing | SQLite (in-memory) | Fast, isolated, cross-platform, async support |
| **Infrastructure** | | |
| Hosting | Railway | Simple deployment, managed Postgres, CI/CD |
| Package Manager | uv | Fast dependency resolution, lock files |
| Process | Single uvicorn | NiceGUI + FastAPI + APScheduler in one process |

### 5.2 Async Architecture

**Why async throughout:**

```python
# FastAPI async endpoint
@router.get("/api/v1/attendance/{session_id}")
async def get_attendance(session_id: str, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        select(AttendanceRecord)
        .where(AttendanceRecord.session_id == session_id)
    )
    return result.scalars().all()

# Background async job
async def activate_deployment(deployment_id: str):
    async with get_db_session() as db:
        await deactivate_current_deployment(db)
        await activate_deployment_by_id(db, deployment_id)
```

**Benefits:**
- Non-blocking I/O operations
- Better concurrent request handling
- Efficient database connection usage
- Works seamlessly with FastAPI's async model

### 5.3 Database Compatibility

**Cross-database compatibility strategy:**

```python
# Works in both SQLite (testing) and PostgreSQL (production)
class User(Base):
    id: Mapped[uuid.UUID] = mapped_column(String(36), primary_key=True)
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict)
```

**SQLite limitations handled:**
- UUIDs stored as String(36) instead of native UUID
- JSON instead of JSONB (automatic serialization)
- Partial indexes implemented at application layer
- Referential integrity via ORM, not DB triggers

---

## 7. Deployment Architecture

### 6.1 Single Instance Deployment (MVP)

```
┌──────────────────────────────────────┐
│        Railway Service               │
│                                       │
│  ┌─────────────────────────────────┐ │
│  │   uvicorn process               │ │
│  │                                 │ │
│  │  ┌─────────────────────────┐   │ │
│  │  │   FastAPI App           │   │ │
│  │  │   - REST API            │   │ │
│  │  │   - NiceGUI Admin       │   │ │
│  │  │   - Static Files        │   │ │
│  │  │   - SSE Endpoints       │   │ │
│  │  │   - APScheduler         │   │ │
│  │  └─────────────────────────┘   │ │
│  │                                 │ │
│  │  ┌─────────────────────────┐   │ │
│  │  │   SQLAlchemy            │   │ │
│  │  │   (connection pool)     │   │ │
│  │  └─────────────────────────┘   │ │
│  └─────────────────────────────────┘ │
│               │                        │
│               │                        │
└───────────────┼────────────────────────┘
                │
                v
┌──────────────────────────────────────┐
│   Railway Managed PostgreSQL          │
│   - Database                        │
│   - APScheduler Job Store            │
└──────────────────────────────────────┘
```

### 6.2 Future Multi-Instance Architecture

```
┌─────────────────────┐    ┌─────────────────────┐
│  Railway Instance 1  │    │  Railway Instance 2  │
│                     │    │                     │
│  ┌───────────────┐  │    │  ┌───────────────┐  │
│  │   FastAPI     │  │    │  │   FastAPI     │  │
│  │   + APScheduler│  │    │  │   + APScheduler│  │
│  └───────────────┘  │    │  └───────────────┘  │
└─────────┬───────────┘    └─────────┬───────────┘
          │                          │
          └──────────┬───────────────┘
                     │
                     v
        ┌────────────────────────────────┐
        │   Shared PostgreSQL             │
        │   - Application Data            │
        │   - APScheduler Job Store       │
        │   (prevents duplicate job runs) │
        └────────────────────────────────┘
```

**No code changes required:**
- APScheduler SQLAlchemy job store prevents duplicate job execution
- Session state stored in database, not memory
- Stateless API design allows horizontal scaling

---

## 8. Security Architecture

### 7.1 Authentication & Authorization

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
│  │     - Google OAuth 2.0                            │  │
│  │     - HttpOnly session cookies                    │  │
│  │     - SameSite=Strict CSRF protection             │  │
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

### 7.2 Access Control Implementation

**Row-level security:**
```python
# User sees personnel rows matching their subunit scope
async def get_visible_personnel(user_id: str, deployment_id: str):
    # Get user's scopes for this deployment
    scopes = await get_user_subunit_scopes(user_id, deployment_id)
    
    # Query personnel matching at least one scope
    personnel = await query_personnel_in_scopes(deployment_id, scopes)
    return personnel
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

### 7.3 Multi-Tenant Deployment Access Control (Phase 5)

**Overview:** Enterprise-grade deployment isolation ensuring users can only access data from deployments they're explicitly authorized to access.

**Architecture:**
```
┌─────────────────────────────────────────────────────────┐
│           Multi-Tenant Access Control                    │
│                                                           │
│  User Request → verify_deployment_access()               │
│       ↓                                                  │
│  Check DeploymentUserAccess table                        │
│       ↓                                                  │
│  Filter data by deployment_id                            │
│       ↓                                                  │
│  Return authorized data only                             │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

**Implementation Pattern:**
```python
async def verify_deployment_access(
    deployment_id: str,
    user_id: str,
    user_role: str,
    db: AsyncSession,
) -> Deployment:
    """Verify user has access to deployment and return it."""
    
    # Super admins have full access
    if user_role == "super_admin":
        return deployment
    
    # Check for explicit deployment access
    access = await db.execute(
        select(DeploymentUserAccess).where(
            and_(
                DeploymentUserAccess.user_id == user_id,
                DeploymentUserAccess.deployment_id == deployment_id,
                DeploymentUserAccess.revoked_at.is_(None),
            )
        )
    )
    
    if not access:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return deployment
```

**Access Control Enforcement Points:**
- ✅ **Personnel API** - Deployment-based listing and filtering
- ✅ **Sessions API** - Session creation and listing restricted by deployment
- ✅ **Attendance API** - Attendance operations respect deployment boundaries
- ✅ **Deployments API** - Deployment management with access checks

**Data Isolation:**
- Users only see personnel from their authorized deployments
- Sessions filtered by deployment access
- Attendance records scoped to accessible deployments
- Automatic filtering in all list operations

**Access Management:**
- `POST /api/v1/access-control/deployments/{id}/users/{user_id}/access` - Grant access
- `DELETE /api/v1/access-control/deployments/{id}/users/{user_id}/access` - Revoke access
- `GET /api/v1/access-control/deployments/{id}/users` - List deployment users
- `GET /api/v1/access-control/users/{user_id}/deployments` - List user deployments

**Security Guarantees:**
- No cross-deployment data leakage
- Explicit access grants required
- Audit trail for all access changes
- Role-based + scope-based authorization

### 7.4 Session Management Implementation

**Session Storage Pattern:**
```python
class UserSession(Base):
    """User authentication session for managing login state and access control."""
    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    # ... other fields
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
1. User logs in via Google OAuth → creates `User` record
2. System creates `UserSession` with secure token → stores in database
3. Client receives token → includes in Authorization header
4. HTTP request triggers `require_authenticated_user()` dependency
5. Dependency validates token via `get_valid_session()` → retrieves UserSession
6. Dependency looks up User by string ID → returns authenticated user
7. Request proceeds with user context

**Security Features:**
- **Token Generation**: `secrets.token_urlsafe(32)` for 256-bit security
- **Expiration**: 7-day default expiration
- **Tracking**: Stores IP, user agent, last accessed time
- **Validation**: Every request validates session in database

---

## 9. Code Standards & Best Practices

### 8.1 Development Patterns

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
- **Characteristics:** Real database (SQLite in-memory), HTTP requests
- **Example:** Testing `POST /api/v1/attendance` with authentication

**Behavioral Tests (`tests/behavioral/`)**
- **Purpose:** Test domain logic and business rules
- **Characteristics:** Database models, constraints, system behavior
- **Example:** Testing access control hierarchy enforcement

### 10.4 Dependency Decisions

**httpx Removal (2026-05-09):**

**Decision:** Removed httpx as a direct dependency from `pyproject.toml`

**Reasons:**
1. **Overengineering:** httpx.AsyncClient provided unnecessary complexity for our testing needs
2. **Framework-native:** FastAPI TestClient is designed specifically for FastAPI applications
3. **Dependency surface:** Reducing direct dependencies improves maintainability
4. **Performance:** TestClient has lower overhead for our use case

**Impact:**
- All integration tests converted from `async_client: AsyncClient` to `client: TestClient`
- Removed `await` keywords from HTTP test calls
- Updated test fixtures to use synchronous interface
- All 100+ integration tests passing with new approach

**Future considerations:**
If httpx.AsyncClient is added back in the future, it should be for specific, intentional reasons:
- **Concurrent request testing** - Testing parallel API calls
- **Load testing** - High concurrency performance testing
- **WebSocket testing** - Advanced WebSocket testing capabilities
- **External async API integration** - When the app needs to make async HTTP calls

This should be a deliberate architectural decision, not incidental complexity.

### 10.5 Test Coverage Requirements

**Minimum Coverage: 80%**

The project requires 80% code coverage across all modules.

**Coverage targets by component:**
- **Utils modules:** 90%+ (isolated, easy to test)
- **API endpoints:** 85%+ (critical paths)
- **Models:** 80%+ (business logic)
- **Middleware:** 75%+ (harder to test)

**Verification:**
```bash
pytest --cov=src/parade_state --cov-report=term-missing
```

---

## 11. Performance & Scalability

### 11.1 Current Performance Characteristics

**Test results:**
- 26 tests execute in ~2 seconds
- Test database: In-memory SQLite
- Coverage: 93.77%

**Expected production performance:**
- API response time: < 200ms for typical queries
- Mobile UI load time: < 2s on 4G
- Attendance write: < 500ms round-trip

### 11.2 Scalability Considerations

**Current single-instance limits:**
- Max concurrent users: ~200 (based on typical FastAPI performance)
- Database connections: Managed by connection pool (default 20 connections)
- Background jobs: APScheduler SQLAlchemy job store ensures safe execution

**Future scaling path:**
1. **Horizontal scaling:** Add more Railway instances
2. **Database scaling:** PostgreSQL read replicas for heavy read operations
3. **Caching:** Redis cache for frequently accessed data (sessions, deployments)
4. **CDN:** Serve static files via CDN for better global performance

---

*End of System Architecture v1.0*
