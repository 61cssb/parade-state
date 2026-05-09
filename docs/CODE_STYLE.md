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

When you need datetime/date types for annotations, import them from utility modules:

```python
# ✅ CORRECT - Type annotations via utilities
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

### **Response Models**

Use Pydantic models for responses:

```python
# ✅ CORRECT - Explicit response models
@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: str):
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
    }

# ❌ VIOLATION - Untyped responses
@router.get("/users/{user_id}")
async def get_user(user_id: str):
    return user  # Unclear what this returns
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

**Remember**: These guidelines exist to improve code quality, not to restrict creativity. When in doubt, prioritize readability and maintainability.

**Questions?** Refer to [CLAUDE.md](../CLAUDE.md) for development patterns or ask the team for clarification.