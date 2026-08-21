"""Admin data purge API endpoint (super-admin only, testing-only).

Deletes every nominal roll and all downstream data so CSV upload can be
re-tested from a clean slate. Users, access levels, sessions, global
column mappings, and the audit log are preserved; the purge itself is
recorded in the audit log. Gated by PURGE_ENABLED (off in production
unless explicitly enabled).
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.config import get_settings
from parade_state.db import get_db_session
from parade_state.models import (
    Attendance,
    AuditLog,
    CsvUpload,
    ColumnMetadata,
    Deferment,
    Grouping,
    GroupingGroup,
    GroupingMemberState,
    GroupingMembership,
    NominalRoll,
    Personnel,
    Tagging,
    TaggingEntry,
    UserSubunitAssignment,
)
from parade_state.utils import utc_dt

router = APIRouter()

CONFIRMATION_WORD = "PURGE"

# Deletion order: children before parents, so FK constraints are satisfied
# on every dialect regardless of their ON DELETE behavior. NominalRoll goes
# last; CsvUpload must precede it (its FK has no ON DELETE action).
PURGE_TABLES: tuple[type, ...] = (
    Attendance,
    Deferment,
    TaggingEntry,
    Tagging,
    GroupingMembership,
    GroupingMemberState,
    GroupingGroup,
    Grouping,
    UserSubunitAssignment,
    ColumnMetadata,
    Personnel,
    CsvUpload,
    NominalRoll,
)


@router.post("/purge")
async def purge_all_data(
    confirmation: str = Query(..., description=f"Must equal {CONFIRMATION_WORD}"),
    user_id: str = Query(..., description="User ID triggering the purge"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete all nominal rolls and downstream data (testing-only).

    Purges attendance, personnel, deferments, taggings, groupings, column
    metadata, CSV uploads, and NR-bound subunit assignments in a single
    transaction, then records the purge in the audit log. Users, access
    levels, sessions, global column mappings, and existing audit entries
    are preserved.

    Requires super_admin role and PURGE_ENABLED on the deployment.
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can purge application data",
        )

    settings = get_settings()
    if not settings.PURGE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data purge is disabled on this deployment (PURGE_ENABLED=false)",
        )

    if confirmation.strip() != CONFIRMATION_WORD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Confirmation text must be {CONFIRMATION_WORD}",
        )

    purged_counts: dict[str, int] = {}
    for model in PURGE_TABLES:
        count = await db.scalar(select(func.count()).select_from(model))
        if count:
            await db.execute(delete(model))
        purged_counts[model.__tablename__] = count or 0

    db.add(
        AuditLog(
            user_id=user_id,
            entity_type="database",
            entity_id="purge",
            action="delete",
            changes=None,
            description=json.dumps(
                {"action": "purge_all_data", "purged_counts": purged_counts},
                default=str,
            ),
        )
    )

    await db.commit()

    return {
        "detail": "All nominal rolls and downstream data purged",
        "purged_at": utc_dt.utcnow().isoformat(),
        "purged_counts": purged_counts,
    }
