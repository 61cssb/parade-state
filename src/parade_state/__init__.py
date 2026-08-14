"""Parade State Management System.

This module implements a battalion parade state management system with access control,
attendance tracking, and grouping management capabilities.

## Module Architecture

The application follows a strict 5-layer architecture with clear dependency boundaries:

### Layer 1: Foundation
- **parade_state.utils** - Core utilities (env, ids, utc_dt)
- **parade_state.db** - Database connection and session management

### Layer 2: Data Models
- **parade_state.models** - SQLAlchemy ORM models and Pydantic schemas

### Layer 3: Business Logic
- **parade_state.config** - Application configuration

### Layer 4: Routes & Authentication
- **parade_state.auth** - Authentication utilities (dependencies, session, oauth)
- **parade_state.web** - User-facing web routes (OAuth flows, redirects)
- **parade_state.api** - REST API route handlers (JSON responses)

### Layer 5: Application
- **parade_state.main** - FastAPI application orchestration

## Dependency Flow

```
Foundation → Data Models → Business Logic → Auth → Web/API → Application
```

**Key Rules:**
- Each layer only depends on lower layers
- No circular dependencies at runtime
- Models use TYPE_CHECKING for forward references
- API endpoints use dependency injection for runtime dependencies
- Web routes and API both use shared auth utilities

## Initialization Order

1. Load utilities (parade_state.utils)
2. Initialize database (parade_state.db.init_database)
3. Import models (parade_state.models)
4. Load business logic (parade_state.config)
5. Load authentication utilities (parade_state.auth)
6. Initialize web routes (parade_state.web) and API routes (parade_state.api)
7. Create FastAPI app (parade_state.main.app)

## Module Dependencies

For detailed dependency analysis and initialization order, see:
- [ARCHITECTURE.md - Module Architecture](../../docs/ARCHITECTURE.md#3-module-architecture)

For development patterns and code conventions, see:
- [CLAUDE.md - Development Guide](../../CLAUDE.md)

## Example Usage

```python
from parade_state.main import app
from parade_state.db import init_database
from parade_state.utils import env

# Initialize database
database_url = env.get("DATABASE_URL")
init_database(database_url)

# Use FastAPI app
# (typically handled by uvicorn directly)
```

## Version

Current version: 0.1.0
"""

__version__ = "0.1.0"
