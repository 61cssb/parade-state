"""Tagging API endpoints.

Super-admin-only CRUD + clone for the Tagging overlay. A tagging is an
overlay of person → subunit remappings on top of a Nominal Roll — it never
mutates the underlying NR's personnel/subunit data.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parade_state.db import get_db_session
from parade_state.models import NominalRoll, Personnel, Tagging, TaggingEntry
from parade_state.models.schemas import (
    TaggingCloneCreate,
    TaggingCloneResponse,
    TaggingCloneUnmatchedItem,
    TaggingCreate,
    TaggingEntryInput,
    TaggingEntryResponse,
    TaggingListItem,
    TaggingResponse,
    TaggingUpdate,
)
from parade_state.utils import utc_dt

router = APIRouter()


# ============================================================================
# Constants & helpers
# ============================================================================


def _require_super_admin(user_role: str) -> None:
    """Authorize super_admin only."""
    if user_role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only super admins can manage taggings",
        )


def _snapshot_from_personnel(personnel: Personnel) -> dict:
    """Snapshot the personnel's canonical subunit for ``from_*`` columns."""
    return {
        "from_unit": personnel.unit,
        "from_sub_unit_1": personnel.sub_unit_1,
        "from_sub_unit_2": personnel.sub_unit_2,
        "from_sub_unit_3": personnel.sub_unit_3,
    }


def _entry_to_response(
    entry: TaggingEntry,
    short_id: str | None = None,
    personnel_label: str | None = None,
) -> TaggingEntryResponse:
    return TaggingEntryResponse(
        id=entry.id,
        tagging_id=entry.tagging_id,
        personnel_id=entry.personnel_id,
        personnel_short_id=short_id,
        personnel_label=personnel_label,
        from_unit=entry.from_unit,
        from_sub_unit_1=entry.from_sub_unit_1,
        from_sub_unit_2=entry.from_sub_unit_2,
        from_sub_unit_3=entry.from_sub_unit_3,
        to_unit=entry.to_unit,
        to_sub_unit_1=entry.to_sub_unit_1,
        to_sub_unit_2=entry.to_sub_unit_2,
        to_sub_unit_3=entry.to_sub_unit_3,
    )


async def _build_entries_response(
    db: AsyncSession, entries: list[TaggingEntry]
) -> list[TaggingEntryResponse]:
    """Build entry responses with personnel short_id/label denormalized."""
    if not entries:
        return []
    personnel_ids = [e.personnel_id for e in entries]
    rows = (
        await db.execute(
            select(Personnel.id, Personnel.short_id, Personnel.rank, Personnel.full_name)
            .where(Personnel.id.in_(personnel_ids))
        )
    ).all()
    info_by_id = {
        row.id: {
            "short_id": row.short_id,
            "label": f"{row.rank} {row.full_name}".strip(),
        }
        for row in rows
    }
    return [
        _entry_to_response(
            e,
            short_id=info_by_id.get(e.personnel_id, {}).get("short_id"),
            personnel_label=info_by_id.get(e.personnel_id, {}).get("label"),
        )
        for e in entries
    ]


def _tagging_to_response(
    tagging: Tagging,
    entries: list[TaggingEntryResponse] | None = None,
) -> TaggingResponse:
    return TaggingResponse(
        id=tagging.id,
        label=tagging.label,
        nominal_roll_id=tagging.nominal_roll_id,
        remarks=tagging.remarks,
        entries=entries or [],
        created_at=tagging.created_at,
        created_by=tagging.created_by,
        updated_at=tagging.updated_at,
        updated_by=tagging.updated_by,
    )


async def _load_personnel_map(
    db: AsyncSession, personnel_ids: list[str]
) -> dict[str, Personnel]:
    """Return a {personnel_id: Personnel} map for the given ids."""
    if not personnel_ids:
        return {}
    rows = (
        await db.execute(
            select(Personnel).where(Personnel.id.in_(personnel_ids))
        )
    ).scalars().all()
    return {str(p.id): p for p in rows}


async def _validate_entries_for_nr(
    db: AsyncSession,
    nominal_roll_id: str,
    entries: list[TaggingEntryInput],
) -> tuple[list[dict], dict[str, Personnel]]:
    """Resolve ``entries`` against ``nominal_roll_id``.

    Returns (entry_payloads, personnel_map) where ``entry_payloads`` is the
    list of ORM-ready dicts (with ``from_*`` snapshotted when omitted) and
    ``personnel_map`` is ``{personnel_id: Personnel}``.

    Raises:
      400: duplicate personnel in input, or personnel not on the NR.
      404: personnel row not found.
    """
    if not entries:
        return [], {}

    personnel_ids = [e.personnel_id for e in entries]
    # Reject duplicate personnel in the same payload (one remap per person).
    seen: set[str] = set()
    for pid in personnel_ids:
        if pid in seen:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Duplicate personnel in request: {pid}. "
                    f"Only one remap per person per tagging."
                ),
            )
        seen.add(pid)

    personnel_map = await _load_personnel_map(db, personnel_ids)
    for pid in personnel_ids:
        if pid not in personnel_map:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Personnel not found: {pid}",
            )
        if personnel_map[pid].nominal_roll_id != nominal_roll_id:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Personnel {pid} does not belong to nominal roll "
                    f"{nominal_roll_id}."
                ),
            )

    payloads: list[dict] = []
    for entry_in in entries:
        payload = {
            "personnel_id": entry_in.personnel_id,
            "to_unit": entry_in.to_unit,
            "to_sub_unit_1": entry_in.to_sub_unit_1,
            "to_sub_unit_2": entry_in.to_sub_unit_2,
            "to_sub_unit_3": entry_in.to_sub_unit_3,
        }
        # If any from_* is explicitly provided, honor all provided from_*
        # fields; otherwise snapshot the personnel's canonical subunit.
        explicit_from = any(
            value is not None
            for value in (
                entry_in.from_unit,
                entry_in.from_sub_unit_1,
                entry_in.from_sub_unit_2,
                entry_in.from_sub_unit_3,
            )
        )
        if explicit_from:
            payload.update(
                {
                    "from_unit": entry_in.from_unit,
                    "from_sub_unit_1": entry_in.from_sub_unit_1,
                    "from_sub_unit_2": entry_in.from_sub_unit_2,
                    "from_sub_unit_3": entry_in.from_sub_unit_3,
                }
            )
        else:
            payload.update(_snapshot_from_personnel(personnel_map[entry_in.personnel_id]))
        payloads.append(payload)
    return payloads, personnel_map


async def _load_tagging_or_404(
    db: AsyncSession, tagging_id: str, *, with_entries: bool = True
) -> Tagging:
    """Fetch a tagging by id; optionally eager-load entries."""
    stmt = select(Tagging).where(Tagging.id == tagging_id)
    if with_entries:
        stmt = stmt.options(selectinload(Tagging.entries))
    tagging = (await db.execute(stmt)).scalar_one_or_none()
    if tagging is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Tagging not found: {tagging_id}",
        )
    return tagging


# ============================================================================
# Endpoints
# ============================================================================


@router.get("", response_model=list[TaggingListItem])
async def list_taggings(
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    nominal_roll_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> list[TaggingListItem]:
    """List taggings, optionally filtered by nominal roll.

    Returns summary rows (no entries); entry counts are computed via a
    correlated subquery so the list view doesn't need to load entries.
    """
    _require_super_admin(user_role)

    entry_count = (
        select(func.count())
        .select_from(TaggingEntry)
        .where(TaggingEntry.tagging_id == Tagging.id)
        .correlate(Tagging)
        .scalar_subquery()
        .label("entry_count")
    )
    query = (
        select(Tagging, entry_count)
        .order_by(Tagging.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if nominal_roll_id:
        query = query.where(Tagging.nominal_roll_id == nominal_roll_id)

    rows = (await db.execute(query)).all()
    return [
        TaggingListItem(
            id=t.id,
            label=t.label,
            nominal_roll_id=t.nominal_roll_id,
            remarks=t.remarks,
            entry_count=count or 0,
            created_at=t.created_at,
            created_by=t.created_by,
            updated_at=t.updated_at,
            updated_by=t.updated_by,
        )
        for t, count in rows
    ]


@router.post(
    "",
    response_model=TaggingResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_tagging(
    payload: TaggingCreate,
    user_id: str = Query(..., description="User ID creating the tagging"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> TaggingResponse:
    """Create a tagging with optional initial entries."""
    _require_super_admin(user_role)

    # Validate NR exists.
    nr = (
        await db.execute(
            select(NominalRoll).where(NominalRoll.id == payload.nominal_roll_id)
        )
    ).scalar_one_or_none()
    if nr is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {payload.nominal_roll_id}",
        )

    entry_payloads, _ = await _validate_entries_for_nr(
        db, payload.nominal_roll_id, payload.entries
    )

    tagging = Tagging(
        label=payload.label.strip(),
        nominal_roll_id=payload.nominal_roll_id,
        remarks=payload.remarks,
        created_by=user_id,
    )
    for ep in entry_payloads:
        tagging.entries.append(TaggingEntry(**ep))

    db.add(tagging)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        detail_msg = (
            f"A tagging with label '{payload.label}' already exists."
            if "label" in str(exc).lower() or "unique" in str(exc).lower()
            else "Tagging could not be created (constraint violation)."
        )
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=detail_msg,
        ) from exc

    # Re-fetch with entries eager-loaded (avoid lazy-load outside async ctx).
    tagging = await _load_tagging_or_404(db, tagging.id, with_entries=True)
    entries_resp = await _build_entries_response(db, tagging.entries)
    return _tagging_to_response(tagging, entries_resp)


@router.get("/{tagging_id}", response_model=TaggingResponse)
async def get_tagging(
    tagging_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> TaggingResponse:
    """Fetch a single tagging by id (with entries)."""
    _require_super_admin(user_role)
    tagging = await _load_tagging_or_404(db, tagging_id, with_entries=True)
    entries_resp = await _build_entries_response(db, tagging.entries)
    return _tagging_to_response(tagging, entries_resp)


@router.patch("/{tagging_id}", response_model=TaggingResponse)
async def update_tagging(
    tagging_id: str,
    payload: TaggingUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> TaggingResponse:
    """Update a tagging.

    Updates label/remarks. If ``entries`` is provided, the tagging's
    entries are full-replaced (existing entries deleted, new ones inserted).
    """
    _require_super_admin(user_role)
    tagging = await _load_tagging_or_404(db, tagging_id, with_entries=True)

    if payload.label is not None:
        tagging.label = payload.label.strip()
    if payload.remarks is not None:
        tagging.remarks = payload.remarks

    if payload.entries is not None:
        entry_payloads, _ = await _validate_entries_for_nr(
            db, tagging.nominal_roll_id, payload.entries
        )
        # Full-replace: clear existing, then append new.
        tagging.entries.clear()
        for ep in entry_payloads:
            tagging.entries.append(TaggingEntry(**ep))

    tagging.updated_at = utc_dt.ensure_naive(utc_dt.utcnow())
    tagging.updated_by = user_id

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"A tagging with label '{payload.label}' already exists."
                if payload.label is not None
                else "Tagging could not be updated (constraint violation)."
            ),
        ) from exc

    # Re-fetch with entries eager-loaded (avoid lazy-load outside async ctx).
    tagging = await _load_tagging_or_404(db, tagging.id, with_entries=True)
    entries_resp = await _build_entries_response(db, tagging.entries)
    return _tagging_to_response(tagging, entries_resp)


@router.delete("/{tagging_id}")
async def delete_tagging(
    tagging_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a tagging. Cascades to entries. Does not mutate the NR.

    Refuses (409) if the tagging is linked to any attendance rows or is the
    active attendance scope for its NR — callers must clone + re-activate
    instead (per issue #4 Q5).
    """
    _require_super_admin(user_role)
    tagging = await _load_tagging_or_404(db, tagging_id, with_entries=False)

    from parade_state.models import Attendance, AttendanceScope

    linked_attendance = (
        await db.execute(
            select(func.count())
            .select_from(Attendance)
            .where(Attendance.tagging_id == tagging_id)
        )
    ).scalar_one()
    if linked_attendance:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"Tagging is linked to {linked_attendance} attendance row(s). "
                "Remove the linkage or clone the tagging instead."
            ),
        )

    linked_scope = (
        await db.execute(
            select(func.count())
            .select_from(AttendanceScope)
            .where(AttendanceScope.tagging_id == tagging_id)
        )
    ).scalar_one()
    if linked_scope:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "Tagging is the active attendance scope for its nominal roll. "
                "Re-activate a different scope before deleting."
            ),
        )

    await db.delete(tagging)
    await db.commit()
    return {"detail": f"Tagging {tagging_id} deleted"}


@router.post("/{tagging_id}/clone", response_model=TaggingCloneResponse)
async def clone_tagging(
    tagging_id: str,
    payload: TaggingCloneCreate,
    user_id: str = Query(..., description="User ID cloning the tagging"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> TaggingCloneResponse:
    """Clone a tagging to a different nominal roll.

    For each source entry, look up the target-NR Personnel row by
    ``short_id`` (the cross-roll person identifier). Matched personnel get a
    new entry on the target NR pointing at the target-NR personnel row;
    unmatched source personnel are surfaced in the response.
    """
    _require_super_admin(user_role)

    source = await _load_tagging_or_404(db, tagging_id, with_entries=True)

    # Validate target NR exists and is distinct.
    target_nr = (
        await db.execute(
            select(NominalRoll).where(
                NominalRoll.id == payload.target_nominal_roll_id
            )
        )
    ).scalar_one_or_none()
    if target_nr is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Target nominal roll not found: {payload.target_nominal_roll_id}",
        )
    if target_nr.id == source.nominal_roll_id:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Target nominal roll must differ from the source nominal roll.",
        )

    # Check label uniqueness up-front for a cleaner error than IntegrityError.
    existing = (
        await db.execute(
            select(Tagging.id).where(Tagging.label == payload.label.strip())
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"A tagging with label '{payload.label}' already exists.",
        )

    # Collect source entries and their personnel rows (for short_id lookup
    # and for naming unmatched personnel in the response).
    source_entries = list(source.entries)
    source_personnel_rows_by_id: dict[str, Personnel] = {}
    if source_entries:
        source_personnel_rows_by_id = await _load_personnel_map(
            db, [e.personnel_id for e in source_entries]
        )

    # Build the map of source_entry → short_id (for matching).
    entry_short_ids: list[str | None] = []
    for entry in source_entries:
        p = source_personnel_rows_by_id.get(entry.personnel_id)
        entry_short_ids.append(p.short_id if p else None)

    # Load target-NR personnel by short_id.
    target_lookup: dict[str, Personnel] = {}
    if entry_short_ids:
        target_rows = (
            await db.execute(
                select(Personnel).where(
                    Personnel.nominal_roll_id == target_nr.id,
                    Personnel.short_id.in_(
                        [sid for sid in entry_short_ids if sid]
                    ),
                )
            )
        ).scalars().all()
        target_lookup = {p.short_id: p for p in target_rows}

    new_tagging = Tagging(
        label=payload.label.strip(),
        nominal_roll_id=target_nr.id,
        remarks=source.remarks,
        created_by=user_id,
    )

    matched_count = 0
    unmatched: list[TaggingCloneUnmatchedItem] = []
    for entry, short_id in zip(source_entries, entry_short_ids, strict=True):
        target_person = target_lookup.get(short_id) if short_id else None
        if target_person is None:
            source_p = source_personnel_rows_by_id.get(entry.personnel_id)
            unmatched.append(
                TaggingCloneUnmatchedItem(
                    short_id=short_id or "",
                    name=(
                        f"{source_p.rank} {source_p.full_name}".strip()
                        if source_p
                        else None
                    ),
                )
            )
            continue

        # Re-snapshot from_* from the TARGET personnel (the source NR's
        # from_* may differ from how the person sits on the target NR).
        target_snapshot = _snapshot_from_personnel(target_person)
        new_tagging.entries.append(
            TaggingEntry(
                personnel_id=target_person.id,
                from_unit=target_snapshot["from_unit"],
                from_sub_unit_1=target_snapshot["from_sub_unit_1"],
                from_sub_unit_2=target_snapshot["from_sub_unit_2"],
                from_sub_unit_3=target_snapshot["from_sub_unit_3"],
                to_unit=entry.to_unit,
                to_sub_unit_1=entry.to_sub_unit_1,
                to_sub_unit_2=entry.to_sub_unit_2,
                to_sub_unit_3=entry.to_sub_unit_3,
            )
        )
        matched_count += 1

    db.add(new_tagging)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"A tagging with label '{payload.label}' already exists."
            ),
        ) from exc

    # Re-fetch with entries eager-loaded (avoid lazy-load outside async ctx).
    new_tagging = await _load_tagging_or_404(
        db, new_tagging.id, with_entries=True
    )
    entries_resp = await _build_entries_response(db, new_tagging.entries)
    return TaggingCloneResponse(
        tagging=_tagging_to_response(new_tagging, entries_resp),
        source_count=len(source_entries),
        matched_count=matched_count,
        unmatched=unmatched,
    )
