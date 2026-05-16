"""FastAPI application setup and configuration."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from parade_state.api import (
    access_control,
    attendance,
    auth,
    deployments,
    personnel,
    sessions,
    users,
)
from parade_state.db import init_database
from parade_state.utils import env
from parade_state.web.auth import router as web_auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    # Startup
    # Only initialize if not already initialized (prevents test database from being reset)
    from parade_state.db import get_session_maker

    if get_session_maker() is None:
        database_url = env.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:") or "sqlite+aiosqlite:///:memory:"
        init_database(database_url)
    yield
    # Shutdown
    pass


app = FastAPI(
    title="Parade State Management System",
    description="Battalion parade state management with access control",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware for mobile UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0"}


# Include routers
# User-facing web routes (OAuth flows, redirects)
app.include_router(web_auth_router, prefix="/auth", tags=["web-auth"])

# REST API endpoints (JSON responses)
app.include_router(auth.router, prefix="/api/v1/auth", tags=["api-auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(
    deployments.router, prefix="/api/v1/deployments", tags=["deployments"]
)
app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
app.include_router(attendance.router, prefix="/api/v1/attendance", tags=["attendance"])
app.include_router(personnel.router, prefix="/api/v1", tags=["personnel"])
app.include_router(
    access_control.router, prefix="/api/v1/access-control", tags=["access-control"]
)
