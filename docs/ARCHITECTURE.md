# System Architecture

**Version:** 1.0  
**Date:** 2026-05-08  
**Status:** Architecture Overview  

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Architecture](#2-component-architecture)
3. [Data Flow](#3-data-flow)
4. [Entity Relationships](#4-entity-relationships)
5. [Technology Stack](#5-technology-stack)

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

## 4. Entity Relationships

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

## 5. Technology Stack

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

## 6. Deployment Architecture

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

## 7. Security Architecture

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

---

## 8. Code Standards & Best Practices

### 8.1 Utility Module Usage

**Always use utility modules instead of native datatypes:**

The `parade_state.utils` package provides centralized utilities that ensure consistency across the application. **Always prefer utility modules over native Python datatypes.**

```python
# ✅ GOOD - Use utility modules
from parade_state.utils import utc_dt

# Get current time
now = utc_dt.utcnow()  # Always timezone-aware UTC
expires = utc_dt.add_timedelta(now, days=7)  # Preserves timezone info

# Check expiration
if utc_dt.is_expired(session.expires_at):
    raise HTTPException(status_code=401, detail="Session expired")

# Database compatibility
db_time = utc_dt.ensure_naive(utc_dt.utcnow())  # SQLite compatible
logic_time = utc_dt.ensure_aware(db_time)  # For business logic

# ❌ BAD - Native datetime usage
from datetime import datetime, timedelta

now = datetime.utcnow()  # Deprecated and timezone-unaware
expires = now + timedelta(days=7)  # Loses timezone info
```

**Why use utility modules:**
- **Consistent timezone handling** - All UTC, all the time
- **Database compatibility** - Proper naive/aware datetime handling
- **Maintainability** - Change behavior in one place
- **Type safety** - Predictable return types
- **Less cognitive load** - Don't think about timezones

**Available utility modules:**
- `utc_dt` - UTC datetime operations (see [UTILS.md](UTILS.md))

For comprehensive utility documentation, see [UTILS.md](UTILS.md).

### 8.2 Async Database Operations

**Always use async database operations:**

```python
# ✅ GOOD - Async database operations
@router.get("/api/v1/users/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

# ❌ BAD - Sync operations in async context
@router.get("/api/v1/users/{user_id}")
def get_user(user_id: str, db: AsyncSession = Depends(get_db_session)):
    user = db.get(User, user_id)  # Blocks the event loop
    return user
```

### 8.3 Type Annotations

**Always use proper type annotations:**

```python
# ✅ GOOD - Complete type annotations
from datetime import datetime
from typing import Optional

async def create_user(
    email: str,
    name: str,
    db: AsyncSession,
) -> User:
    user = User(email=email, name=name)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

# ❌ BAD - Missing type annotations
async def create_user(email, name, db):
    user = User(email=email, name=name)
    db.add(user)
    await db.commit()
    return user
```

### 8.4 Error Handling

**Use proper HTTP status codes and error messages:**

```python
# ✅ GOOD - Descriptive errors
from fastapi import HTTPException, status

if not user:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="User not found",
        headers={"X-Error": "User lookup failed"},
    )

# ❌ BAD - Generic errors
if not user:
    raise HTTPException(status_code=404, detail="Error")
```

### 8.5 Database Session Management

**Always use dependency injection for database sessions:**

```python
# ✅ GOOD - Dependency injection
@router.post("/api/v1/users")
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db_session),  # Injected by FastAPI
):
    user = User(**user_data.dict())
    db.add(user)
    await db.commit()
    return user

# ❌ BAD - Manual session creation
@router.post("/api/v1/users")
async def create_user(user_data: UserCreate):
    async with get_db_session() as db:  # Bypasses FastAPI's dependency system
        user = User(**user_data.dict())
        db.add(user)
        await db.commit()
        return user
```

---

## 10. Performance & Scalability

### 8.1 Current Performance Characteristics

**Test results:**
- 26 tests execute in ~2 seconds
- Test database: In-memory SQLite
- Coverage: 93.77%

**Expected production performance:**
- API response time: < 200ms for typical queries
- Mobile UI load time: < 2s on 4G
- Attendance write: < 500ms round-trip

### 8.2 Scalability Considerations

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
