"""API router package."""

from parade_state.api import (
    access_control,
    attendance,
    auth,
    deployments,
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
]
