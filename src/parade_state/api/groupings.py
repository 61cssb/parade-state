"""Grouping management API endpoints (issue 26 redesign).

A grouping is a labelled, closed vocabulary of groups based on the
attendance-active nominal roll. Servicemen on that roll hold memberships
in the groups plus a per-grouping checkbox and free-text remarks.
Groupings never read or write attendance.

All mutations are super-admin only (403 otherwise); reads are open to
every role. Groupings whose nominal roll is not the attendance-active
one are unreachable (404) until their roll is re-activated.
"""

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parade_state.db import get_db_session
from parade_state.models import (
    AuditLog,
    Grouping,
    GroupingGroup,
    GroupingMemberState,
    GroupingMembership,
    NominalRoll,
    Personnel,
)
from parade_state.models.schemas import (
    GroupingCloneRequest,
    GroupingCopyRequest,
    GroupingCreate,
    GroupingGroupItem,
    GroupingGroupResponse,
    GroupingResponse,
    GroupingUpdate,
    MemberStateUpdate,
    MembershipSetRequest,
)
from parade_state.utils import utc_dt

router = APIRouter()


# ============================================================================
# Helpers
# ============================================================================


def _require_super_admin(user_role: str) -> None:
    """Authorize super_admin only."""
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can manage groupings",
        )


async def _active_nr(db: AsyncSession) -> NominalRoll | None:
    """The nominal roll currently active for attendance, if any."""
    return (
        await db.execute(
            select(NominalRoll).where(NominalRoll.attendance_active.is_(True))
        )
    ).scalar_one_or_none()


async def _load_grouping(grouping_id: str, db: AsyncSession) -> Grouping:
    """Load a grouping (with children) that lives on the active NR.

    Groupings based on non-active NRs are retained in the DB but not
    reachable: their roll must be (re-)activated first.
    """
    nr = await _active_nr(db)
    if nr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No nominal roll is active for attendance",
        )
    grouping = (
        await db.execute(
            select(Grouping)
            .where(
                Grouping.id == grouping_id,
                Grouping.nominal_roll_id == nr.id,
            )
            .options(
                selectinload(Grouping.groups).selectinload(GroupingGroup.memberships),
                selectinload(Grouping.memberships),
                selectinload(Grouping.member_state),
            )
        )
    ).scalar_one_or_none()
    if grouping is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grouping not found on the nominal roll active for attendance",
        )
    return grouping


async def _member_counts(db: AsyncSession, grouping_id: str) -> dict[str, int]:
    """Membership count per group id — feeds the removal warning popup."""
    rows = await db.execute(
        select(GroupingMembership.group_id, func.count())
        .where(GroupingMembership.grouping_id == grouping_id)
        .group_by(GroupingMembership.group_id)
    )
    return {group_id: count for group_id, count in rows.all()}


def _to_response(grouping: Grouping, counts: dict[str, int]) -> GroupingResponse:
    # Sort by position: the in-memory collection's iteration order is the
    # session's insertion order, not the display order.
    return GroupingResponse(
        id=grouping.id,
        label=grouping.label,
        nominal_roll_id=grouping.nominal_roll_id,
        multiple_membership=grouping.multiple_membership,
        allow_ungrouped=grouping.allow_ungrouped,
        groups=[
            GroupingGroupResponse(
                id=g.id,
                label=g.label,
                position=g.position,
                member_count=counts.get(g.id, 0),
            )
            for g in sorted(grouping.groups, key=lambda g: g.position)
        ],
        created_at=grouping.created_at,
        created_by=grouping.created_by,
    )


async def _ensure_label_available(
    db: AsyncSession, label: str, nominal_roll_id: str, *, exclude_id: str | None = None
) -> None:
    """Labels are unique per nominal roll — a copy from a previous roll
    may keep its label while the source still exists on that old roll."""
    query = select(Grouping.id).where(
        Grouping.label == label,
        Grouping.nominal_roll_id == nominal_roll_id,
    )
    if exclude_id is not None:
        query = query.where(Grouping.id != exclude_id)
    if (await db.execute(query)).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Grouping label already in use on this nominal roll.",
        )


def _check_group_labels_unique(labels: list[str]) -> None:
    seen: set[str] = set()
    for label in labels:
        if label in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate group label: {label!r}",
            )
        seen.add(label)


async def _fetch_for_response(
    db: AsyncSession, grouping_id: str
) -> Grouping:
    """Re-fetch a grouping with its children eagerly loaded.

    After an insert, an untouched ``groups`` collection would lazy-load
    (and crash under async); this keeps response building IO-explicit.
    """
    grouping = (
        await db.execute(
            select(Grouping)
            .where(Grouping.id == grouping_id)
            .options(selectinload(Grouping.groups))
        )
    ).scalar_one()
    return grouping


def _audit(
    db: AsyncSession,
    user_id: str,
    grouping: Grouping,
    action: str,
    description: dict,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            entity_type="grouping",
            entity_id=str(grouping.id),
            action=action,
            changes=None,
            description=json.dumps(description, default=str),
        )
    )


def _apply_group_set(grouping: Grouping, items: list[GroupingGroupItem]) -> None:
    """Apply a full group-enum payload: add, rename, reorder, remove.

    Removals cascade memberships away via the ORM, except where
    ``allow_ungrouped=false`` would leave a serviceman with no group —
    those are rejected first with the affected count.
    """
    _check_group_labels_unique([item.label for item in items])

    existing = {group.id: group for group in grouping.groups}

    # Unknown ids in the payload are a client bug, not a removal.
    for item in items:
        if item.id is not None and item.id not in existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown group id: {item.id}",
            )

    kept_ids = {item.id for item in items if item.id is not None}

    if not grouping.allow_ungrouped:
        # A serviceman stays grouped if they hold at least one membership
        # in a group that survives this update.
        affected = 0
        for group in grouping.groups:
            if group.id in kept_ids:
                continue
            for membership in group.memberships:
                survivor_ids = {
                    m.group_id
                    for m in grouping.memberships
                    if m.personnel_id == membership.personnel_id
                } & kept_ids
                if not survivor_ids:
                    affected += 1
        if affected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot remove: {affected} servicemen would be left "
                    "without a group (this grouping requires every "
                    "serviceman to hold at least one). Reassign them first."
                ),
            )

    # Removals first (ORM cascades their memberships away).
    for group in list(grouping.groups):
        if group.id not in kept_ids:
            grouping.groups.remove(group)

    # Adds + renames + positions.
    by_position: dict[int, GroupingGroup] = {}
    for position, item in enumerate(items):
        if item.id is not None:
            group = existing[item.id]
            group.label = item.label
        else:
            group = GroupingGroup(label=item.label)
            grouping.groups.append(group)
        group.position = position
        by_position[position] = group


# ============================================================================
# Grouping CRUD
# ============================================================================


@router.post("/", response_model=GroupingResponse, status_code=status.HTTP_201_CREATED)
async def create_grouping(
    grouping_data: GroupingCreate,
    user_id: str = Query(..., description="User ID creating the grouping"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a grouping on the nominal roll active for attendance."""
    _require_super_admin(user_role)

    nr = await _active_nr(db)
    if nr is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No nominal roll is active for attendance.",
        )

    await _ensure_label_available(db, grouping_data.label, str(nr.id))
    _check_group_labels_unique([item.label for item in grouping_data.groups])

    grouping = Grouping(
        label=grouping_data.label,
        nominal_roll_id=nr.id,
        multiple_membership=grouping_data.multiple_membership,
        allow_ungrouped=grouping_data.allow_ungrouped,
        created_by=user_id,
    )
    for position, item in enumerate(grouping_data.groups):
        grouping.groups.append(
            GroupingGroup(label=item.label, position=position)
        )

    db.add(grouping)
    _audit(
        db,
        user_id,
        grouping,
        "create",
        {
            "label": grouping.label,
            "groups": [item.label for item in grouping_data.groups],
            "multiple_membership": grouping.multiple_membership,
            "allow_ungrouped": grouping.allow_ungrouped,
        },
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Grouping label already in use.",
        ) from None
    return _to_response(
        await _fetch_for_response(db, grouping.id),
        await _member_counts(db, grouping.id),
    )


@router.get("/", response_model=list[GroupingResponse])
async def list_groupings(
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """List the groupings on the attendance-active NR."""
    nr = await _active_nr(db)
    if nr is None:
        return []
    groupings = (
        (
            await db.execute(
                select(Grouping)
                .where(Grouping.nominal_roll_id == nr.id)
                .options(selectinload(Grouping.groups))
                .order_by(Grouping.created_at)
            )
        )
        .scalars()
        .all()
    )
    result = []
    for grouping in groupings:
        counts = await _member_counts(db, grouping.id)
        result.append(_to_response(grouping, counts))
    return result


@router.get("/{grouping_id}", response_model=GroupingResponse)
async def get_grouping(
    grouping_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get one grouping on the attendance-active NR."""
    grouping = await _load_grouping(grouping_id, db)
    return _to_response(grouping, await _member_counts(db, grouping.id))


@router.patch("/{grouping_id}", response_model=GroupingResponse)
async def update_grouping(
    grouping_id: str,
    update_data: GroupingUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update a grouping's label and group enums.

    ``multiple_membership`` / ``allow_ungrouped`` are immutable after
    creation — change attempts get a 400 pointing at clone-and-replace.
    """
    _require_super_admin(user_role)
    grouping = await _load_grouping(grouping_id, db)

    if update_data.multiple_membership is not None and (
        update_data.multiple_membership != grouping.multiple_membership
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="multiple_membership cannot be changed after creation.",
        )
    if update_data.allow_ungrouped is not None and (
        update_data.allow_ungrouped != grouping.allow_ungrouped
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="allow_ungrouped cannot be changed after creation.",
        )

    if update_data.label is not None and update_data.label != grouping.label:
        await _ensure_label_available(
            db,
            update_data.label,
            grouping.nominal_roll_id,
            exclude_id=grouping.id,
        )
        grouping.label = update_data.label

    if update_data.groups is not None:
        _apply_group_set(grouping, update_data.groups)

    _audit(
        db,
        user_id,
        grouping,
        "update",
        {"label": grouping.label},
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Grouping label already in use.",
        ) from None
    return _to_response(grouping, await _member_counts(db, grouping.id))


@router.delete("/{grouping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_grouping(
    grouping_id: str,
    user_id: str = Query(..., description="User ID making the deletion"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a grouping; groups, memberships and member state cascade."""
    _require_super_admin(user_role)
    grouping = await _load_grouping(grouping_id, db)

    _audit(db, user_id, grouping, "delete", {"label": grouping.label})
    await db.delete(grouping)
    await db.commit()
    return None


# ============================================================================
# Memberships and member state
# ============================================================================


@router.put(
    "/{grouping_id}/personnel/{personnel_id}/groups",
    response_model=GroupingResponse,
)
async def set_personnel_groups(
    grouping_id: str,
    personnel_id: str,
    payload: MembershipSetRequest,
    user_id: str = Query(..., description="User ID making the change"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Set a serviceman's full group membership set within a grouping."""
    _require_super_admin(user_role)
    grouping = await _load_grouping(grouping_id, db)

    personnel = (
        await db.execute(
            select(Personnel).where(
                Personnel.id == personnel_id,
                Personnel.nominal_roll_id == grouping.nominal_roll_id,
            )
        )
    ).scalar_one_or_none()
    if personnel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Serviceman not found on this grouping's nominal roll.",
        )

    group_ids: list[str] = []
    for group_id in payload.group_ids:
        if group_id not in group_ids:
            group_ids.append(group_id)

    known = {group.id for group in grouping.groups}
    for group_id in group_ids:
        if group_id not in known:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown group id for this grouping.",
            )

    if not grouping.multiple_membership and len(group_ids) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This grouping allows only one group per serviceman.",
        )
    if not group_ids and not grouping.allow_ungrouped:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This grouping requires every serviceman to hold a group.",
        )

    wanted = set(group_ids)
    for membership in list(grouping.memberships):
        if membership.personnel_id == personnel_id and membership.group_id not in wanted:
            grouping.memberships.remove(membership)
    held = {
        membership.group_id
        for membership in grouping.memberships
        if membership.personnel_id == personnel_id
    }
    for group_id in group_ids:
        if group_id not in held:
            grouping.memberships.append(
                GroupingMembership(
                    group_id=group_id,
                    personnel_id=personnel_id,
                )
            )

    await db.commit()
    return _to_response(grouping, await _member_counts(db, grouping.id))


@router.patch("/{grouping_id}/personnel/{personnel_id}/state")
async def update_member_state(
    grouping_id: str,
    personnel_id: str,
    payload: MemberStateUpdate,
    user_id: str = Query(..., description="User ID making the change"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update a serviceman's grouping checkbox / free-text remarks.

    Both fields are intentionally generic — their meaning is left to the
    unit's standardisation.
    """
    _require_super_admin(user_role)
    grouping = await _load_grouping(grouping_id, db)

    personnel = (
        await db.execute(
            select(Personnel).where(
                Personnel.id == personnel_id,
                Personnel.nominal_roll_id == grouping.nominal_roll_id,
            )
        )
    ).scalar_one_or_none()
    if personnel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Serviceman not found on this grouping's nominal roll.",
        )

    state = (
        await db.execute(
            select(GroupingMemberState).where(
                GroupingMemberState.grouping_id == grouping_id,
                GroupingMemberState.personnel_id == personnel_id,
            )
        )
    ).scalar_one_or_none()
    if state is None:
        state = GroupingMemberState(
            grouping_id=grouping_id,
            personnel_id=personnel_id,
            updated_by=user_id,
        )
        db.add(state)
    if payload.checkbox is not None:
        state.checkbox = payload.checkbox
    if payload.remarks is not None:
        state.remarks = payload.remarks or None
    state.updated_by = user_id
    state.updated_at = utc_dt.db_utcnow()

    await db.commit()
    return {"detail": "Member state updated"}


# ============================================================================
# Clone and copy-from-previous-NR
# ============================================================================


@router.post("/{grouping_id}/clone", response_model=GroupingResponse,
             status_code=status.HTTP_201_CREATED)
async def clone_grouping(
    grouping_id: str,
    payload: GroupingCloneRequest,
    user_id: str = Query(..., description="User ID making the clone"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Clone a grouping on the same NR under a fresh label.

    Structure (group enums with positions + both flags) always carries
    over; memberships and member state only when the dialog opts in.
    """
    _require_super_admin(user_role)
    source = await _load_grouping(grouping_id, db)
    await _ensure_label_available(db, payload.label, source.nominal_roll_id)

    clone = Grouping(
        label=payload.label,
        nominal_roll_id=source.nominal_roll_id,
        multiple_membership=source.multiple_membership,
        allow_ungrouped=source.allow_ungrouped,
        created_by=user_id,
    )
    group_map: dict[str, GroupingGroup] = {}
    for group in source.groups:
        copy = GroupingGroup(label=group.label, position=group.position)
        clone.groups.append(copy)
        group_map[group.id] = copy

    if payload.include_memberships:
        for membership in source.memberships:
            clone.memberships.append(
                GroupingMembership(
                    group=group_map[membership.group_id],
                    personnel_id=membership.personnel_id,
                )
            )
        for state in source.member_state:
            clone.member_state.append(
                GroupingMemberState(
                    personnel_id=state.personnel_id,
                    checkbox=state.checkbox,
                    remarks=state.remarks,
                    updated_by=user_id,
                )
            )

    db.add(clone)
    _audit(
        db,
        user_id,
        clone,
        "create",
        {"cloned_from": source.label, "include_memberships": payload.include_memberships},
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Grouping label already in use.",
        ) from None
    return _to_response(
        await _fetch_for_response(db, clone.id),
        await _member_counts(db, clone.id),
    )


@router.post("/copy-from-previous", response_model=GroupingResponse,
             status_code=status.HTTP_201_CREATED)
async def copy_grouping_from_previous_nr(
    payload: GroupingCopyRequest,
    user_id: str = Query(..., description="User ID making the copy"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Copy a grouping from the previously activated NR onto the active one.

    Group enums (with positions) and both flags carry over. Memberships
    re-link by ``pers_no`` — the canonical cross-roll person identifier —
    so new-NR personnel without a match start ungrouped. Member state is
    not copied: checkbox / remarks are per-cycle operational state.
    """
    _require_super_admin(user_role)

    active = await _active_nr(db)
    if active is None or active.attendance_activated_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No nominal roll is active for attendance.",
        )

    previous = (
        await db.execute(
            select(NominalRoll)
            .where(
                NominalRoll.attendance_activated_at.is_not(None),
                NominalRoll.attendance_activated_at < active.attendance_activated_at,
            )
            .order_by(NominalRoll.attendance_activated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if previous is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No previously activated nominal roll to copy from.",
        )

    source = (
        await db.execute(
            select(Grouping)
            .where(
                Grouping.id == payload.source_grouping_id,
                Grouping.nominal_roll_id == previous.id,
            )
            .options(
                selectinload(Grouping.groups),
                selectinload(Grouping.memberships),
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grouping not found on the previously activated nominal roll.",
        )

    label = payload.label or source.label
    await _ensure_label_available(db, label, str(active.id))

    copy = Grouping(
        label=label,
        nominal_roll_id=active.id,
        multiple_membership=source.multiple_membership,
        allow_ungrouped=source.allow_ungrouped,
        created_by=user_id,
    )
    group_map: dict[str, GroupingGroup] = {}
    for group in source.groups:
        group_copy = GroupingGroup(label=group.label, position=group.position)
        copy.groups.append(group_copy)
        group_map[group.id] = group_copy

    # Re-link memberships by pers_no — relationships wire the FKs, so no
    # intermediate flush is needed.
    new_roll_people = {
        person.pers_no: person.id
        for person in (
            await db.execute(
                select(Personnel).where(
                    Personnel.nominal_roll_id == active.id,
                    Personnel.status == "active",
                    Personnel.pers_no.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    }
    source_people = {
        person.id: person.pers_no
        for person in (
            await db.execute(
                select(Personnel).where(
                    Personnel.nominal_roll_id == previous.id
                )
            )
        )
        .scalars()
        .all()
    }
    relinked = 0
    for membership in source.memberships:
        pers_no = source_people.get(membership.personnel_id)
        target_id = new_roll_people.get(pers_no) if pers_no else None
        if target_id is None:
            continue  # no pers_no match — starts ungrouped
        copy.memberships.append(
            GroupingMembership(
                group=group_map[membership.group_id],
                personnel_id=target_id,
            )
        )
        relinked += 1

    db.add(copy)
    _audit(
        db,
        user_id,
        copy,
        "create",
        {
            "copied_from": source.label,
            "source_nominal_roll_id": str(previous.id),
            "relinked_memberships": relinked,
        },
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Grouping label already in use.",
        ) from None
    return _to_response(
        await _fetch_for_response(db, copy.id),
        await _member_counts(db, copy.id),
    )


# ============================================================================
# CSV Export
# ============================================================================


@router.get("/{grouping_id}/export")
async def export_grouping_csv(
    grouping_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Export the grouping table exactly as displayed.

    Columns: Group, Rank, Name, Unit, Sub Unit, Checkbox, Remarks. No
    attendance data — groupings never interact with attendance.
    """
    grouping = await _load_grouping(grouping_id, db)

    personnel_rows = (
        (
            await db.execute(
                select(Personnel)
                .where(
                    Personnel.nominal_roll_id == grouping.nominal_roll_id,
                    Personnel.status == "active",
                )
                .order_by(
                    Personnel.unit,
                    Personnel.sub_unit_1,
                    Personnel.rank,
                    Personnel.full_name,
                )
            )
        )
        .scalars()
        .all()
    )

    memberships = (
        await db.execute(
            select(GroupingMembership).where(
                GroupingMembership.grouping_id == grouping.id
            )
        )
    )
    group_labels = {group.id: group.label for group in grouping.groups}
    groups_by_person: dict[str, list[str]] = {}
    for membership in memberships.scalars():
        groups_by_person.setdefault(membership.personnel_id, []).append(
            group_labels.get(membership.group_id, "?")
        )

    states = (
        await db.execute(
            select(GroupingMemberState).where(
                GroupingMemberState.grouping_id == grouping.id
            )
        )
    )
    state_by_person = {state.personnel_id: state for state in states.scalars()}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Group", "Rank", "Name", "Unit", "Sub Unit", "Checkbox", "Remarks"]
    )
    for person in personnel_rows:
        state = state_by_person.get(person.id)
        writer.writerow(
            [
                "; ".join(groups_by_person.get(person.id, [])),
                person.rank,
                person.full_name,
                person.unit,
                person.sub_unit_1 or "",
                "Yes" if state and state.checkbox else "",
                state.remarks if state and state.remarks else "",
            ]
        )

    csv_data = output.getvalue()
    output.close()
    filename = (
        f"grouping_{grouping.label.replace(' ', '_')}_"
        f"{utc_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    return StreamingResponse(
        io.BytesIO(csv_data.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
