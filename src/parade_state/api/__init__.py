"""API router package."""

from parade_state.api import (
    access_control,
    attendance,
    audit,
    auth,
    csv_upload,
    groupings,
    nominal_rolls,
    personnel,
    sessions,
    tagging,
    users,
)

__all__ = [
    "auth",
    "users",
    "groupings",
    "sessions",
    "attendance",
    "personnel",
    "access_control",
    "csv_upload",
    "nominal_rolls",
    "audit",
    "tagging",
]
