"""Subunit-1 attendance access enforcement (issue #4 PR 2).

Attendance writes are gated per Nominal Roll: a user may only upsert attendance
for personnel whose *effective* ``sub_unit_1`` matches one of their
``UserSubunitAssignment`` rows on that NR. The effective sub_unit_1 is the
active Tagging overlay's ``to_sub_unit_1`` when a tagging is the active scope
(taggings are "remappings already in use"), falling back to the personnel's
canonical ``sub_unit_1``. ``super_admin`` bypasses entirely. Deny-by-default:
a user with no assignments on an NR has no attendance-write access there.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import Personnel, TaggingEntry, UserSubunitAssignment


async def get_assigned_subunit_1s(
    db: AsyncSession, user_id: str, nominal_roll_id: str
) -> set[str]:
    """Return the set of sub_unit_1 strings the user is assigned to on the NR."""
    result = await db.execute(
        select(UserSubunitAssignment.sub_unit_1).where(
            UserSubunitAssignment.user_id == user_id,
            UserSubunitAssignment.nominal_roll_id == nominal_roll_id,
        )
    )
    return {row[0] for row in result.all()}


async def resolve_effective_subunit_1_map(
    db: AsyncSession,
    personnel_ids: list[str],
    active_tagging_id: str | None,
) -> dict[str, str | None]:
    """Map each personnel_id → effective sub_unit_1.

    Effective = the active tagging's ``to_sub_unit_1`` if a TaggingEntry exists
    for that person, else the personnel's canonical ``sub_unit_1``.
    """
    if not personnel_ids:
        return {}

    personnel_result = await db.execute(
        select(Personnel.id, Personnel.sub_unit_1).where(
            Personnel.id.in_(personnel_ids)
        )
    )
    canonical: dict[str, str | None] = {
        str(pid): sub1 for pid, sub1 in personnel_result.all()
    }

    if not active_tagging_id:
        return canonical

    remap_result = await db.execute(
        select(TaggingEntry.personnel_id, TaggingEntry.to_sub_unit_1).where(
            TaggingEntry.tagging_id == active_tagging_id,
            TaggingEntry.personnel_id.in_(personnel_ids),
        )
    )
    remap: dict[str, str | None] = {
        str(pid): sub1 for pid, sub1 in remap_result.all()
    }

    return {pid: remap.get(pid, canonical.get(pid)) for pid in canonical}


async def assert_can_update_attendance(
    db: AsyncSession,
    nominal_roll_id: str,
    user_id: str,
    user_role: str,
    personnel_ids: list[str],
    active_tagging_id: str | None,
) -> dict[str, str | None]:
    """Enforce Subunit-1 access for a write touching the given personnel.

    Returns the effective-sub_unit_1 map (reused by the caller for snapshotting).
    Raises 403 listing the offending sub_unit_1s if the user lacks any.
    ``super_admin`` bypasses the check (map still returned).
    """
    effective_map = await resolve_effective_subunit_1_map(
        db, personnel_ids, active_tagging_id
    )

    if user_role == "super_admin":
        return effective_map

    allowed = await get_assigned_subunit_1s(db, user_id, nominal_roll_id)
    # Deny-by-default: no assignments = no access.
    blocked = {sub for sub in effective_map.values() if sub and sub not in allowed}
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "No Subunit-1 assignment for: "
                + ", ".join(sorted(blocked))
                + ". Ask a super-admin to grant access."
            ),
        )
    return effective_map
