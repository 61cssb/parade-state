# Parade State Code Style Guide

**Purpose:** Define coding standards and conventions for the Parade State application to ensure consistency, maintainability, and quality across the codebase.

**Table of Contents:**
1. [Utility Module Encapsulation](#utility-module-encapsulation)
2. [Import Conventions](#import-conventions)
3. [Code Organization](#code-organization)
4. [Type Annotations](#type-annotations)
5. [Error Handling](#error-handling)
6. [Database Operations](#database-operations)
7. [API Endpoint Design](#api-endpoint-design)

---

## Utility Module Encapsulation

### 🔒 **Core Principle: No Direct Built-in Module Imports**

**DO NOT** import built-in modules directly in application code:
```python
# ❌ VIOLATION
import datetime
import os
import uuid
from datetime import datetime, date
```

**DO** use centralized utility modules instead:
```python
# ✅ CORRECT
from parade_state.utils import utc_dt
from parade_state.utils import env
from parade_state.utils import ids

# For type annotations
def schedule_session(date: utc_dt.date) -> utc_dt.datetime:
    session_date = utc_dt.utcnow()
    return session_date
```

### **Why This Pattern?**

1. **Centralized Logic**: Change behavior in one place affects entire codebase
2. **Testability**: Easy to mock utilities for testing
3. **Consistency**: Same operations produce same results everywhere
4. **Error Prevention**: Avoid common pitfalls (timezone confusion, path issues)
5. **Documentation**: Utility modules document business logic patterns

### **Available Utility Modules**

| Module | Purpose | Key Types/Functions |
|--------|---------|---------------------|
| `utc_dt` | Datetime operations | `utc_dt.datetime`, `utc_dt.date`, `utc_dt.utcnow()` |
| `env` | Environment variables | `env.get()`, `env.get_url()`, `env.get_bool()` |
| `ids` | Identifier generation | `ids.uuid4()`, `ids.uuid4_str()`, `ids.is_valid()` |

### **Type Annotations Using Utilities**

When you need datetime/date types for annotations, always use the module-qualified form:

```python
# ✅ CORRECT - Type annotations via module-qualified references
from parade_state.utils import utc_dt

def create_session(
    session_date: utc_dt.date,
    start_time: utc_dt.datetime,
    end_time: utc_dt.datetime,
) -> Session:
    """Create a new session with proper datetime handling."""
    now = utc_dt.utcnow()
    session = Session(
        date=session_date,
        created_at=now,
        valid_from=start_time,
        valid_until=end_time,
    )
    return session

# ❌ VIOLATION - Direct datetime import
from datetime import datetime, date

def create_session(
    session_date: date,  # VIOLATION
    start_time: datetime,  # VIOLATION
) -> Session:
    pass
```

### 🔒 **Module-Qualified References Only**

Never import sub-attributes (types, functions, classes) directly from utility modules. Always reference them through the module namespace.

```python
# ✅ CORRECT - Module-qualified reference
from parade_state.utils import utc_dt

created_at: Mapped[utc_dt.datetime] = mapped_column(default=lambda: utc_dt.ensure_naive(utc_dt.utcnow()))

# ❌ VIOLATION - Importing sub-attributes from utility module
from parade_state.utils.utc_dt import datetime, date

created_at: Mapped[datetime] = mapped_column(...)
```

**Why?** When another developer or agent sees `datetime` in code without the import line visible, they correctly assume it's the built-in — but they won't realize it's a re-export from `utc_dt` that may change in future. The `utc_dt.` prefix makes the dependency explicit and visible at every call site.

**This rule applies to all utility modules:** `utc_dt`, `ids`, `env`, `cookies`

**Exception:** Within `src/parade_state/utils/` submodules themselves, direct access is allowed since they ARE the utility implementations.

---

## Import Conventions

### **Import Organization**

Organize imports in this exact order:

1. **Standard library** (avoid when utility modules exist)
2. **Third-party imports** (FastAPI, SQLAlchemy, etc.)
3. **Local application imports** (from parade_state.*)

```python
# ✅ CORRECT import order
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import User
from parade_state.utils import utc_dt, ids
```

### **Module-Level Imports Preferred**

```python
# ✅ GOOD - Module-level imports
from parade_state.utils import utc_dt

def process_session():
    now = utc_dt.utcnow()
    return now

# ❌ AVOID - Function-level imports (unless resolving naming conflicts)
def process_session():
    from parade_state.utils.utc_dt import utcnow  # Unnecessary
    return utcnow()
```

---

## Code Organization

### **File Structure**

```
parade_state/
├── api/              # API endpoints (organized by feature)
├── models/           # Database models
├── middleware/       # Custom middleware
├── utils/            # Utility modules (centralized logic)
├── db/               # Database configuration
└── schemas/          # Pydantic schemas
```

### **Feature-Based Organization**

Group related functionality:

```python
# ✅ GOOD - Feature-specific file
# parade_state/api/sessions.py - All session-related endpoints
@router.post("/sessions")
async def create_session(...):
    pass

@router.get("/sessions/{session_id}")
async def get_session(...):
    pass

# ❌ AVOID - Unrelated endpoints in same file
# parade_state/api/mixed.py - Don't mix sessions, users, deployments
```

---

## Type Annotations

### **Complete Type Annotations Required**

Every function must have complete type annotations:

```python
# ✅ CORRECT - Complete annotations
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

# ❌ VIOLATION - Missing types
async def create_user(email, name, db):  # What types?
    pass
```

### **Union Types for Optional Values**

Use modern `X | None` syntax instead of `Optional[X]`:

```python
# ✅ PREFERRED - Modern syntax
def update_user(
    user_id: str,
    name: str | None = None,
    email: str | None = None,
) -> User:
    pass

# ⚠️ ACCEPTABLE - Legacy syntax (being phased out)
from typing import Optional

def update_user(
    user_id: str,
    name: Optional[str] = None,
) -> User:
    pass
```

---

## Error Handling

### **Explicit HTTP Status Codes**

Use specific, descriptive HTTP status codes:

```python
# ✅ CORRECT - Specific error codes
if not user:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="User not found",
    )

if user.status != "active":
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"User account is {user.status}",
    )

# ❌ VIOLATION - Generic errors
if not user:
    raise HTTPException(status_code=404, detail="Error")  # What error?
```

### **Exception Chaining**

Use proper exception chaining to preserve error context:

```python
# ✅ CORRECT - Preserve error context
try:
    user_id = ids.to_uuid(user_id_str)
except ValueError as e:
    raise HTTPException(
        status_code=400,
        detail="Invalid user ID format",
    ) from e  # Preserve original error

# ⚠️ ACCEPTABLE - Explicit suppression
try:
    process_data(data)
except ValidationError:
    raise HTTPException(
        status_code=400,
        detail="Invalid data format",
    ) from None  # Intentionally suppress original error
```

---

## Database Operations

### **Async Database Operations Required**

Always use async database operations:

```python
# ✅ CORRECT - Async operations
@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

# ❌ VIOLATION - Blocking sync operations
@router.get("/users/{user_id}")
def get_user(user_id: str, db: AsyncSession = Depends(get_db_session)):
    user = db.get(User, user_id)  # Blocks event loop!
    return user
```

### **Dependency Injection for Sessions**

Always use FastAPI's dependency injection:

```python
# ✅ CORRECT - FastAPI dependency injection
@router.post("/users")
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db_session),  # Injected
):
    pass

# ❌ VIOLATION - Manual session creation
async def create_user(user_data: dict):
    async with get_db_session() as db:  # Bypasses FastAPI
        pass
```

---

## API Endpoint Design

### **RESTful Conventions**

Follow RESTful conventions:

```python
# ✅ CORRECT - RESTful routes
@router.get("/users")              # List users
@router.get("/users/{user_id}")    # Get specific user
@router.post("/users")             # Create user
@router.patch("/users/{user_id}") # Update user
@router.delete("/users/{user_id}") # Delete user

# ❌ VIOLATION - Non-RESTful routes
@router.get("/get_all_users")      # Not RESTful
@router.get("/create_user")        # Wrong HTTP method
```

### **HTTP Methods and Status Codes**

Use appropriate HTTP methods and status codes:

| Operation | HTTP Method | Success Status | Error Statuses |
|-----------|-------------|----------------|----------------|
| List | GET | 200 OK | 400, 401, 403, 404 |
| Get by ID | GET | 200 OK | 400, 401, 403, 404 |
| Create | POST | 201 Created | 400, 401, 403 |
| Update | PATCH | 200 OK | 400, 401, 403, 404 |
| Delete | DELETE | 204 No Content | 400, 401, 403, 404 |

---

## Database Models

### **Naming Conventions**

**Table Names:**
- Use `snake_case` (plural for tables)
- Example: `personnel`, `deployment_user_accesses`

**Column Names:**
- Use `snake_case`
- Example: `created_at`, `deployment_id`, `full_name`

**Relationships:**
- Use `relationship()` with clear `back_populates`
- Example:
```python
class Personnel(Base):
    """Individual personnel record."""
    __tablename__ = "personnel"

    # Relationships
    deployment_overrides: Mapped[list["DeploymentPersonnelOverride"]] = relationship(
        back_populates="personnel",
        cascade="all, delete-orphan",
    )
```

**Indexes:**
- Add indexes for frequently queried columns
- Example:
```python
class Personnel(Base):
    short_id: Mapped[str] = mapped_column(String(8), index=True)  # cross-estab person identity
    rank: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", name="personnel_status"),
        index=True,
    )
```

### **Foreign Key Conventions**

Store UUIDs as strings for database compatibility:

```python
# ✅ CORRECT - String UUIDs
class Deployment(Base):
    __tablename__ = "deployments"

    estab_id: Mapped[str] = mapped_column(
        String(36),  # String UUID for SQLite compatibility
        ForeignKey("estabs.id", ondelete="CASCADE"),
    )

# ❌ AVOID - Native UUID type (not SQLite compatible)
class Deployment(Base):
    estab_id: Mapped[UUID] = mapped_column(UUID)  # Not portable
```

---

## Enforcing Style Guidelines

### **Automated Checks**

The project uses automated tools to enforce these guidelines:

1. **Ruff** - Linting and formatting
   ```bash
   ruff check src/     # Check for violations
   ruff format src/    # Auto-format code
   ```

2. **Pre-commit Hooks** - Run before commits
   ```bash
   ruff check --fix src/  # Auto-fix violations
   ruff format src/       # Format code
   ```

### **Common Violations to Watch For**

| Violation | Detection | Fix |
|-----------|-----------|-----|
| `import datetime` | Manual review | Use `from parade_state.utils import utc_dt` |
| `import uuid` | Manual review | Use `from parade_state.utils import ids` |
| `import os` | Manual review | Use `from parade_state.utils import env` |
| Missing type annotations | `ruff check` | Add complete type hints |
| Sync database operations | `ruff check` | Use async/await |

---

## Future Maintenance

### **Dependency Security**

**Recommended Addition:**
- **`pip-audit`** - Automated vulnerability scanning for dependencies
- Add to CI/CD pipeline for automated security checks
- Run manually: `pip-audit` to check for known vulnerabilities

**Benefits:**
- Automated security vulnerability detection
- Dependency monitoring for security patches
- Compliance with security best practices

**Implementation:**
```bash
# Install pip-audit
uv add --dev pip-audit

# Run security audit
uv run pip-audit

# CI/CD integration
- name: Security audit
  run: uv run pip-audit
```

### **Version Management**

**Current Approach:**
- Development: `>=` constraints for flexibility with security updates
- Production: Consider pinning major versions for stability
- Review: Quarterly dependency audit recommended

**Update Policy:**
- Keep dependencies current for security patches
- Test upgrades in development before production deployment
- Monitor breaking changes in major version updates

### **Documentation Maintenance**

**Keep Current:**
- [ ] Update [CODE_STYLE.md](CODE_STYLE.md) when introducing new patterns
- [ ] Update [CLAUDE.md](../CLAUDE.md) with new development patterns
- [ ] Update [IMPLEMENTATION.md](IMPLEMENTATION.md) with architectural changes
- [ ] Review and update quarterly for accuracy

---

**Remember**: These guidelines exist to improve code quality, not to restrict creativity. When in doubt, prioritize readability and maintainability.

**Questions?** Refer to [CLAUDE.md](../CLAUDE.md) for development patterns or ask the team for clarification.