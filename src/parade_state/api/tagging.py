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


async def _load_nr_tagging(
    db: AsyncSession, nominal_roll_id: str, *, with_entries: bool = True
) -> Tagging | None:
    """Fetch the 1:1 tagging for ``nominal_roll_id`` (or None)."""
    stmt = select(Tagging).where(Tagging.nominal_roll_id == nominal_roll_id)
    if with_entries:
        stmt = stmt.options(selectinload(Tagging.entries))
    return (await db.execute(stmt)).scalar_one_or_none()


async def copy_entries_by_short_id(
    db: AsyncSession,
    source_tagging: Tagging,
    target_tagging: Tagging,
    target_nominal_roll_id: str,
) -> tuple[int, list[TaggingCloneUnmatchedItem]]:
    """Copy ``source_tagging.entries`` into ``target_tagging`` by ``short_id``.

    Used by the clone endpoint and the CSV-process "import taggings" flow.
    Matches each source entry's personnel ``short_id`` against target-NR
    personnel. Matched personnel get a new entry on the target tagging
    pointing at the target-NR personnel row; ``from_*`` is re-snapshotted
    from the target personnel. Personnel that already have an entry on the
    target tagging are skipped (no clobber). Source personnel with no
    ``short_id`` match in the target NR are surfaced in the return value.

    Returns ``(matched_count, unmatched)``.
    """
    source_entries = list(source_tagging.entries)
    if not source_entries:
        return 0, []

    # Load source personnel rows (for short_id lookup + naming unmatched).
    source_personnel = await _load_personnel_map(
        db, [e.personnel_id for e in source_entries]
    )
    entry_short_ids: list[str | None] = [
        source_personnel[e.personnel_id].short_id
        if e.personnel_id in source_personnel
        else None
        for e in source_entries
    ]

    # Load target-NR personnel keyed by short_id.
    valid_short_ids = [sid for sid in entry_short_ids if sid]
    target_lookup: dict[str, Personnel] = {}
    if valid_short_ids:
        target_rows = (
            await db.execute(
                select(Personnel).where(
                    Personnel.nominal_roll_id == target_nominal_roll_id,
                    Personnel.short_id.in_(valid_short_ids),
                )
            )
        ).scalars().all()
        target_lookup = {p.short_id: p for p in target_rows}

    # Load personnel_ids already on the target tagging (skip to avoid clobber).
    existing_target_personnel_ids = {
        e.personnel_id for e in target_tagging.entries
    }

    matched_count = 0
    unmatched: list[TaggingCloneUnmatchedItem] = []
    for entry, short_id in zip(source_entries, entry_short_ids, strict=True):
        target_person = target_lookup.get(short_id) if short_id else None
        if target_person is None:
            source_p = source_personnel.get(entry.personnel_id)
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
        if target_person.id in existing_target_personnel_ids:
            # Target tagging already has an entry for this person — skip.
            continue

        target_snapshot = _snapshot_from_personnel(target_person)
        target_tagging.entries.append(
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
        existing_target_personnel_ids.add(target_person.id)
        matched_count += 1

    return matched_count, unmatched


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
    """Create a tagging with optional initial entries.

    Under the 1:1 model taggings are auto-created on NR ingestion — this
    endpoint exists to backfill NRs that predate the auto-creation flow.
    A 409 is returned if the NR already has a tagging.
    """
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

    existing = await _load_nr_tagging(db, payload.nominal_roll_id, with_entries=False)
    if existing is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "Nominal roll already has a tagging (1:1). "
                "Use PATCH /taggings/{id} to update it."
            ),
        )

    entry_payloads, _ = await _validate_entries_for_nr(
        db, payload.nominal_roll_id, payload.entries
    )

    tagging = Tagging(
        label=payload.label.strip() if payload.label is not None else None,
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
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Tagging could not be created (constraint violation).",
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
            detail="Tagging could not be updated (constraint violation).",
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

    Refuses (409) if the tagging's NR has any attendance rows — deleting
    would orphan the recorded history (per issue #4 Q5; under the 1:1 model
    the NR's attendance rows are the linkage).
    """
    _require_super_admin(user_role)
    tagging = await _load_tagging_or_404(db, tagging_id, with_entries=False)

    from parade_state.models import Attendance

    linked_attendance = (
        await db.execute(
            select(func.count())
            .select_from(Attendance)
            .where(Attendance.nominal_roll_id == tagging.nominal_roll_id)
        )
    ).scalar_one()
    if linked_attendance:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"The tagging's nominal roll has {linked_attendance} "
                "attendance row(s). Deleting would orphan that history."
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
    """Merge source tagging's entries into a target NR's existing tagging.

    Under the 1:1 model every NR already has a tagging, so "clone" no longer
    creates a new tagging — it merges the source's entries into the target
    NR's tagging by ``short_id`` matching. Personnel already on the target
    tagging are skipped (no clobber); source personnel with no short_id
    match in the target NR are surfaced in the response.
    """
    _require_super_admin(user_role)

    source = await _load_tagging_or_404(db, tagging_id, with_entries=True)

    # Validate target NR exists and is distinct from the source.
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

    # Load the target NR's existing tagging (auto-created on ingest; if
    # missing, treat that as a 404 — the NR predates auto-creation and
    # needs its tagging backfilled via POST /taggings first).
    target_tagging = await _load_nr_tagging(db, target_nr.id, with_entries=True)
    if target_tagging is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=(
                f"Target nominal roll {target_nr.id} has no tagging. "
                "Create one via POST /taggings first."
            ),
        )

    source_count = len(source.entries)
    matched_count, unmatched = await copy_entries_by_short_id(
        db, source, target_tagging, target_nr.id
    )

    if matched_count:
        target_tagging.updated_at = utc_dt.ensure_naive(utc_dt.utcnow())
        target_tagging.updated_by = user_id

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Tagging merge failed (constraint violation).",
        ) from exc

    # Re-fetch with entries eager-loaded (avoid lazy-load outside async ctx).
    target_tagging = await _load_tagging_or_404(
        db, target_tagging.id, with_entries=True
    )
    entries_resp = await _build_entries_response(db, target_tagging.entries)
    return TaggingCloneResponse(
        tagging=_tagging_to_response(target_tagging, entries_resp),
        source_count=source_count,
        matched_count=matched_count,
        unmatched=unmatched,
    )
