"""API router package."""

from parade_state.api import (
    access_control,
    attendance,
    audit,
    auth,
    csv_upload,
    deployments,
    estabs,
    personnel,
    sessions,
    users,
)

__all__ = [
    "auth",
    "users",
    "deployments",
    "sessions",
    "attendance",
    "personnel",
    "access_control",
    "csv_upload",
    "estabs",
    "audit",
]
