"""Shared scope enforcement — the admin-tier access seam (issues #4, #28, #31).

A ``UserSubunitAssignment`` grant authorizes reads and writes for
personnel whose *effective* location matches the grant. The effective
location is the NR's 1:1 Tagging overlay remap (``to_unit`` /
``to_sub_unit_1``) when an entry exists for the person — entry values
apply verbatim, including NULLs — falling back to the personnel row's
canonical ``unit`` / ``sub_unit_1``. Grants use the explicit ``*``
sentinel for wildcard matching (never an empty string, so accidental
blanks fail validation instead of widening access):

- ``(unit='U', sub_unit_1='*')`` — every sub-unit of unit U
- ``(unit='U', sub_unit_1='S')`` — exactly U/S
- ``(unit='*', sub_unit_1='S')`` — S under any unit (pre-#28 grants)
- ``(unit='*', sub_unit_1='*')`` — forbidden by CHECK constraint

``super_admin`` bypasses every check. Everyone else is deny-by-default:
no grants on an NR means no access there, and a personnel row with a
NULL effective sub_unit_1 matches only ``sub_unit_1='*'`` grants.

**The seam (issue #31):** caller identity currently arrives as explicit
``user_id`` / ``user_role`` arguments — today supplied by (spoofable)
query parameters at the API edge. Every scope decision in the codebase
must flow through this module so that switching to session-derived
identity only changes how these arguments are sourced, never how scope
is computed.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import Personnel, TaggingEntry, UserSubunitAssignment

WILDCARD = "*"


@dataclass(frozen=True)
class ScopeGrant:
    """One grant: ``unit``/``sub_unit_1`` with ``*`` meaning wildcard."""

    unit: str
    sub_unit_1: str


# ============================================================================
# Pure matching helpers
# ============================================================================


def grant_matches(
    grants: Iterable[ScopeGrant], eff_unit: str | None, eff_sub1: str | None
) -> bool:
    """True when any grant covers the effective location (unit, sub_unit_1).

    ``None`` effective values never equal a concrete grant value, so they
    only match wildcard columns — a deliberate fix for the pre-#28 hole
    where NULL sub_unit_1 personnel slipped through deny-by-default.
    """
    for grant in grants:
        if grant.unit != WILDCARD and grant.unit != eff_unit:
            continue
        if grant.sub_unit_1 != WILDCARD and grant.sub_unit_1 != eff_sub1:
            continue
        return True
    return False


def scope_conditions(grants: Iterable[ScopeGrant]) -> ColumnElement[bool]:
    """SQL WHERE fragment matching Personnel rows against the grants.

    Uses canonical ``unit``/``sub_unit_1`` — only valid when no tagging
    overlay applies to the queried rows (callers with an active overlay
    must filter via :func:`resolve_effective_locations` in Python
    instead). A NULL ``sub_unit_1`` matches only wildcard-subunit grants,
    mirroring :func:`grant_matches`.
    """
    clauses = [
        and_(
            Personnel.unit == grant.unit if grant.unit != WILDCARD else true(),
            (
                Personnel.sub_unit_1 == grant.sub_unit_1
                if grant.sub_unit_1 != WILDCARD
                else true()
            ),
        )
        for grant in grants
    ]
    if not clauses:
        return false()
    return or_(*clauses)


def location_label(eff_unit: str | None, eff_sub1: str | None) -> str:
    """Human-readable "unit/sub-unit" label for 403 messages and the UI."""
    unit = eff_unit if eff_unit else "(no unit)"
    sub1 = eff_sub1 if eff_sub1 else "(no sub-unit)"
    return f"{unit}/{sub1}"


def _deny_detail(blocked_labels: Iterable[str]) -> str:
    return (
        "No assignment for: "
        + ", ".join(sorted(blocked_labels))
        + ". Ask a super-admin to grant access."
    )


# ============================================================================
# Grant loading
# ============================================================================


async def get_scope_grants(
    db: AsyncSession, user_id: str, nominal_roll_id: str
) -> list[ScopeGrant]:
    """The user's scope grants on one NR (empty list = deny-by-default)."""
    result = await db.execute(
        select(
            UserSubunitAssignment.unit, UserSubunitAssignment.sub_unit_1
        ).where(
            UserSubunitAssignment.user_id == user_id,
            UserSubunitAssignment.nominal_roll_id == nominal_roll_id,
        )
    )
    return [ScopeGrant(unit=unit, sub_unit_1=sub1) for unit, sub1 in result.all()]


async def accessible_nr_ids(
    db: AsyncSession, user_id: str, user_role: str
) -> set[str] | None:
    """NR ids the user has at least one grant on.

    Returns ``None`` for ``super_admin`` (unrestricted — no filtering);
    everyone else gets the exact set, possibly empty.
    """
    if user_role == "super_admin":
        return None
    result = await db.execute(
        select(UserSubunitAssignment.nominal_roll_id).where(
            UserSubunitAssignment.user_id == user_id
        )
    )
    return {row[0] for row in result.all()}


# ============================================================================
# Effective-location resolution (tagging overlay → canonical fallback)
# ============================================================================


async def resolve_effective_locations(
    db: AsyncSession,
    personnel_ids: list[str],
    active_tagging_id: str | None,
) -> dict[str, tuple[str | None, str | None]]:
    """Map each personnel_id → effective ``(unit, sub_unit_1)``.

    A TaggingEntry for the person overrides both values verbatim (a NULL
    ``to_unit``/``to_sub_unit_1`` means the entry cleared that field);
    personnel without an entry use their canonical values.
    """
    if not personnel_ids:
        return {}

    personnel_result = await db.execute(
        select(Personnel.id, Personnel.unit, Personnel.sub_unit_1).where(
            Personnel.id.in_(personnel_ids)
        )
    )
    canonical: dict[str, tuple[str | None, str | None]] = {
        str(pid): (unit, sub1) for pid, unit, sub1 in personnel_result.all()
    }

    if not active_tagging_id:
        return canonical

    remap_result = await db.execute(
        select(
            TaggingEntry.personnel_id, TaggingEntry.to_unit, TaggingEntry.to_sub_unit_1
        ).where(
            TaggingEntry.tagging_id == active_tagging_id,
            TaggingEntry.personnel_id.in_(personnel_ids),
        )
    )
    remap: dict[str, tuple[str | None, str | None]] = {
        str(pid): (to_unit, to_sub1) for pid, to_unit, to_sub1 in remap_result.all()
    }

    return {pid: remap.get(pid, loc) for pid, loc in canonical.items()}


# ============================================================================
# Enforcement (write path — raising)
# ============================================================================


async def assert_nr_accessible(
    db: AsyncSession, user_id: str, user_role: str, nominal_roll_id: str
) -> None:
    """Deny-by-default gate for NR-level access (403 with zero grants)."""
    if user_role == "super_admin":
        return
    grants = await get_scope_grants(db, user_id, nominal_roll_id)
    if not grants:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "No assignments on this nominal roll. "
                "Ask a super-admin to grant access."
            ),
        )


async def assert_locations_in_scope(
    db: AsyncSession,
    user_id: str,
    user_role: str,
    nominal_roll_id: str,
    personnel_ids: list[str],
    active_tagging_id: str | None,
) -> dict[str, tuple[str | None, str | None]]:
    """Enforce scope for a write touching the given personnel.

    Returns the effective-location map (callers reuse it for
    snapshotting). Raises 403 listing the offending "unit/sub-unit"
    locations — with zero grants every location is blocked, so
    deny-by-default falls out naturally. ``super_admin`` bypasses (map
    still returned).
    """
    locations = await resolve_effective_locations(
        db, personnel_ids, active_tagging_id
    )

    if user_role == "super_admin":
        return locations

    grants = await get_scope_grants(db, user_id, nominal_roll_id)
    blocked = {
        location_label(unit, sub1)
        for unit, sub1 in locations.values()
        if not grant_matches(grants, unit, sub1)
    }
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_deny_detail(blocked),
        )
    return locations


# ============================================================================
# Filtering (read path — non-raising)
# ============================================================================


async def in_scope_pids(
    db: AsyncSession,
    user_id: str,
    user_role: str,
    nominal_roll_id: str,
    active_tagging_id: str | None,
    all_pids: Iterable[str],
) -> set[str] | None:
    """Which of ``all_pids`` the user may see on this NR.

    Returns ``None`` when unrestricted (``super_admin``); otherwise the
    in-scope subset (possibly empty). Read endpoints that must
    deny-by-default call :func:`assert_nr_accessible` first — with zero
    grants this returns an empty set rather than raising, so UI surfaces
    can render an explanatory empty state instead of an error.
    """
    if user_role == "super_admin":
        return None

    pid_list = list(all_pids)
    if not pid_list:
        return set()

    grants = await get_scope_grants(db, user_id, nominal_roll_id)
    if not grants:
        return set()

    locations = await resolve_effective_locations(
        db, pid_list, active_tagging_id
    )
    return {
        pid
        for pid in pid_list
        if grant_matches(grants, *locations.get(pid, (None, None)))
    }
