"""FastAPI application setup and configuration."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from parade_state.db import init_database
from parade_state.api import auth, users, deployments, sessions, attendance


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    # Startup
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
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
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(deployments.router, prefix="/api/v1/deployments", tags=["deployments"])
app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
app.include_router(attendance.router, prefix="/api/v1/attendance", tags=["attendance"])