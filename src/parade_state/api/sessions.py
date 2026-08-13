"""Deprecated session management endpoints.

The user-managed Session model has been removed (attendance is now AM/PM
hardcoded and scoped to an NR/Tagging). These routes remain as 410 Gone
signposts so stale clients get a clear signal.
"""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

router = APIRouter()


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
)
async def sessions_gone(path: str, request: Request):  # noqa: ARG001
    """Return 410 Gone for any /api/v1/sessions/* path."""
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={
            "detail": "Sessions have been removed. Attendance is now "
            "AM/PM-hardcoded and scoped to a Nominal Roll / Tagging. "
            "See /api/v1/attendance/."
        },
    )
