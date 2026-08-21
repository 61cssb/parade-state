"""Personnel management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import (
    PRESENT_LIKE_STATUSES,
    Attendance,
    AuditLog,
    NominalRoll,
    Personnel,
    SOURCE_MANUAL,
    Tagging,
    TaggingEntry,
)
from parade_state.models.schemas import (
    PersonnelAttendanceHistoryItem,
    PersonnelAttendanceHistoryResponse,
    PersonnelAttendanceHistoryStats,
    PersonnelCreate,
    PersonnelListParams,
    PersonnelResponse,
    PersonnelUpdate,
)
from parade_state.utils import ranks, utc_dt

router = APIRouter()


# Fields in PersonnelUpdate that move with the person (identity). The NR is
# read-only under the 1:1 tagging model — these cannot be edited via PATCH.
_IDENTITY_FIELDS: frozenset[str] = frozenset({"rank", "name"})

# Fields in PersonnelUpdate that represent a unit/subunit remap. These are
# redirected to a TaggingEntry on the personnel's NR tagging.
_REMAP_FIELDS: frozenset[str] = frozenset(
    {"unit", "sub_unit_1", "sub_unit_2", "sub_unit_3"}
)


# ============================================================================
# Helper Functions
# ============================================================================


# Maps PersonnelUpdate field names to TaggingEntry "to_*" columns.
_REMAP_FIELD_TO_ENTRY_COLUMN: dict[str, str] = {
    "unit": "to_unit",
    "sub_unit_1": "to_sub_unit_1",
    "sub_unit_2": "to_sub_unit_2",
    "sub_unit_3": "to_sub_unit_3",
}


def _apply_remap_to_existing_entry(
    entry: TaggingEntry, remap_updates: dict[str, str | None]
) -> None:
    """Merge ``remap_updates`` into an existing entry's ``to_*`` columns.

    Only fields present in ``remap_updates`` are touched; existing values
    for unmentioned fields are preserved. ``None`` clears the target field.
    """
    for field, column in _REMAP_FIELD_TO_ENTRY_COLUMN.items():
        if field in remap_updates:
            setattr(entry, column, remap_updates[field])


async def _redirect_remap_to_tagging_entry(
    db: AsyncSession,
    personnel: Personnel,
    remap_updates: dict[str, str | None],
    user_id: str,
) -> None:
    """Upsert a TaggingEntry on the personnel's NR tagging for the given remap.

    The NR is read-only — unit/subunit edits are recorded as a tagging entry
    overlay. If an entry already exists for this person, the new remap fields
    are merged into it (unmentioned fields preserved). If the NR has no
    tagging yet (legacy data), one is auto-created.

    ``to_unit`` is required on TaggingEntry; when the caller doesn't supply
    a unit, the entry's existing ``to_unit`` (or the personnel's canonical
    unit) is used as the starting point.
    """
    if not remap_updates:
        return

    # Load (or auto-create) the NR's 1:1 tagging.
    tagging = (
        await db.execute(
            select(Tagging).where(Tagging.nominal_roll_id == personnel.nominal_roll_id)
        )
    ).scalar_one_or_none()
    if tagging is None:
        tagging = Tagging(
            nominal_roll_id=personnel.nominal_roll_id,
            created_by=user_id,
        )
        db.add(tagging)
        await db.flush()

    # Load existing entry for this person (if any).
    entry = (
        await db.execute(
            select(TaggingEntry).where(
                TaggingEntry.tagging_id == tagging.id,
                TaggingEntry.personnel_id == personnel.id,
            )
        )
    ).scalar_one_or_none()

    if entry is None:
        # Seed to_* from canonical personnel values, then apply requested updates.
        to_unit = personnel.unit
        to_sub_1 = personnel.sub_unit_1
        to_sub_2 = personnel.sub_unit_2
        to_sub_3 = personnel.sub_unit_3
        if "unit" in remap_updates:
            to_unit = remap_updates["unit"]
        if "sub_unit_1" in remap_updates:
            to_sub_1 = remap_updates["sub_unit_1"]
        if "sub_unit_2" in remap_updates:
            to_sub_2 = remap_updates["sub_unit_2"]
        if "sub_unit_3" in remap_updates:
            to_sub_3 = remap_updates["sub_unit_3"]

        entry = TaggingEntry(
            tagging_id=tagging.id,
            personnel_id=personnel.id,
            from_unit=personnel.unit,
            from_sub_unit_1=personnel.sub_unit_1,
            from_sub_unit_2=personnel.sub_unit_2,
            from_sub_unit_3=personnel.sub_unit_3,
            to_unit=to_unit,
            to_sub_unit_1=to_sub_1,
            to_sub_unit_2=to_sub_2,
            to_sub_unit_3=to_sub_3,
        )
        db.add(entry)
    else:
        _apply_remap_to_existing_entry(entry, remap_updates)

    tagging.updated_at = utc_dt.ensure_naive(utc_dt.utcnow())
    tagging.updated_by = user_id


async def _load_effective_remap_for_personnel(
    db: AsyncSession, personnel: Personnel
) -> TaggingEntry | None:
    """Return the TaggingEntry overlaying this person, if any."""
    return (
        await db.execute(
            select(TaggingEntry).where(TaggingEntry.personnel_id == personnel.id)
        )
    ).scalar_one_or_none()


def apply_personnel_filters(query, params: PersonnelListParams):
    """Apply filters to personnel query."""
    # Filter by nominal_roll_id
    if params.nominal_roll_id:
        query = query.where(Personnel.nominal_roll_id == params.nominal_roll_id)

    # Filter by status
    if params.status:
        query = query.where(Personnel.status == params.status)

    # Filter by unit hierarchy
    if params.unit:
        query = query.where(Personnel.unit == params.unit)

    if params.sub_unit_1:
        query = query.where(Personnel.sub_unit_1 == params.sub_unit_1)

    if params.sub_unit_2:
        query = query.where(Personnel.sub_unit_2 == params.sub_unit_2)

    if params.sub_unit_3:
        query = query.where(Personnel.sub_unit_3 == params.sub_unit_3)

    # Filter by category (Officer / WOSE)
    if params.category:
        query = query.where(Personnel.category == params.category)

    # Search across name and pers_no
    if params.search:
        search_term = f"%{params.search}%"
        query = query.where(
            or_(
                Personnel.full_name.ilike(search_term),
                Personnel.pers_no.ilike(search_term),
            )
        )

    # Apply sorting
    if params.sort_by:
        # Map sort_by parameter to actual model fields
        sort_field_map = {
            "name": Personnel.full_name,
            "rank": Personnel.rank,
            "unit": Personnel.unit,
            "status": Personnel.status,
            "created_at": Personnel.created_at,
            "updated_at": Personnel.updated_at,
        }

        if params.sort_by in sort_field_map:
            sort_field = sort_field_map[params.sort_by]

            # Apply sort order
            if params.sort_order == "desc":
                query = query.order_by(sort_field.desc())
            else:
                query = query.order_by(sort_field.asc())

    return query


# ============================================================================
# Personnel Endpoints
# ============================================================================


@router.get("/personnel", response_model=list[PersonnelResponse])
async def list_personnel(
    nominal_roll_id: str | None = Query(
        None, description="Filter by nominal roll ID"
    ),
    unit: str | None = Query(None, description="Filter by unit"),
    sub_unit_1: str | None = Query(None, description="Filter by sub-unit 1"),
    sub_unit_2: str | None = Query(None, description="Filter by sub-unit 2"),
    sub_unit_3: str | None = Query(None, description="Filter by sub-unit 3"),
    status: str | None = Query(None, description="Filter by personnel status"),
    category: str | None = Query(
        None, description="Filter by category (Officer, WOSE)"
    ),
    search: str | None = Query(None, description="Search by name or service number"),
    sort_by: str | None = Query(
        None,
        description="Sort field (name, rank, unit, status, created_at, updated_at)",
    ),
    sort_order: str | None = Query(None, description="Sort order (asc, desc)"),
    limit: int = Query(100, ge=1, le=1000, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """List personnel with filtering and sorting (admin/super_admin only).

    Sorting:
    - Can sort by: name, rank, unit, status, created_at, updated_at
    - Sort order: asc (ascending) or desc (descending)
    - Default: No sorting (returns in natural order)
    """
    params = PersonnelListParams(
        nominal_roll_id=nominal_roll_id,
        unit=unit,
        sub_unit_1=sub_unit_1,
        sub_unit_2=sub_unit_2,
        sub_unit_3=sub_unit_3,
        status=status,
        category=category,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )

    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can list personnel",
        )

    query = select(Personnel)
    query = apply_personnel_filters(query, params)

    # Apply pagination
    query = query.offset(params.offset).limit(params.limit)

    # Execute query
    result = await db.execute(query)
    personnel_list = result.scalars().all()

    personnel_responses = [
        PersonnelResponse(
            id=p.id,
            nominal_roll_id=p.nominal_roll_id,
            pers_no=p.pers_no,
            rank=p.rank,
            category=p.category,
            name=p.full_name,
            unit=p.unit,
            sub_unit_1=p.sub_unit_1,
            sub_unit_2=p.sub_unit_2,
            sub_unit_3=p.sub_unit_3,
            status=p.status,
            callup_status=p.callup_status,
            remarks=p.remarks,
            source=p.source,
            created_at=p.created_at,
            updated_at=p.updated_at,
            created_by=p.created_by,
            updated_by=p.updated_by,
        )
        for p in personnel_list
    ]

    return personnel_responses


@router.post(
    "/personnel",
    response_model=PersonnelResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_personnel(
    personnel_create: PersonnelCreate,
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Manually add a serviceman to a nominal roll (super-admin only).

    Covers the gap where a person is missing from the ingested CSV: the row
    is created with ``source="manual"`` and otherwise behaves like any other
    serviceman (attendance, callup/remarks editing, groupings). ``pers_no``
    may be NULL when not yet known — the per-roll unique constraint treats
    NULLs as distinct, and a super-admin can fill it in later via PATCH.
    Manual adds live only on the roll they were added to; the next CSV
    upload creates a new roll that will not include them.
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only super-admins can add personnel manually",
        )

    nr = (
        await db.execute(
            select(NominalRoll).where(
                NominalRoll.id == personnel_create.nominal_roll_id
            )
        )
    ).scalar_one_or_none()
    if nr is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Nominal roll not found",
        )

    # Category is inferred from the rank — never manually set.
    try:
        category = ranks.category_for_rank(personnel_create.rank)
    except ValueError as exc:
        valid_ranks = ", ".join(sorted(ranks.OFFICER_RANKS | ranks.WOSE_RANKS))
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid rank: {personnel_create.rank!r}. Valid ranks: "
                f"{valid_ranks} (or ME1-ME9)"
            ),
        ) from exc

    # Per-roll pers_no uniqueness pre-check (matches CSV semantics: the same
    # pers_no on a *different* roll remains allowed).
    if personnel_create.pers_no is not None:
        duplicate = (
            await db.execute(
                select(Personnel).where(
                    Personnel.nominal_roll_id == str(nr.id),
                    Personnel.pers_no == personnel_create.pers_no,
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=(
                    f"Personnel with pers_no {personnel_create.pers_no} "
                    "already exists on this nominal roll"
                ),
            )

    personnel = Personnel(
        nominal_roll_id=str(nr.id),
        pers_no=personnel_create.pers_no,
        rank=personnel_create.rank,
        category=category,
        full_name=personnel_create.name,
        unit=personnel_create.unit,
        sub_unit_1=personnel_create.sub_unit_1,
        sub_unit_2=personnel_create.sub_unit_2,
        sub_unit_3=personnel_create.sub_unit_3,
        callup_status=personnel_create.callup_status or "Called Up",
        remarks=personnel_create.remarks,
        source=SOURCE_MANUAL,
        created_by=user_id,
    )
    db.add(personnel)
    await db.flush()

    nr.personnel_count = (nr.personnel_count or 0) + 1
    db.add(
        AuditLog(
            user_id=user_id,
            entity_type="personnel",
            entity_id=str(personnel.id),
            action="create",
            description=(
                f"Manually added {personnel_create.rank} {personnel_create.name} "
                f"(pers_no {personnel_create.pers_no or 'unknown'}) to nominal "
                f"roll CAA {nr.caa.isoformat()}"
            ),
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # Race on the unique constraint despite the pre-check above.
        await db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"Personnel with pers_no {personnel_create.pers_no} "
                "already exists on this nominal roll"
            ),
        )
    await db.refresh(personnel)

    return PersonnelResponse(
        id=personnel.id,
        nominal_roll_id=personnel.nominal_roll_id,
        pers_no=personnel.pers_no,
        rank=personnel.rank,
        category=personnel.category,
        name=personnel.full_name,
        unit=personnel.unit,
        sub_unit_1=personnel.sub_unit_1,
        sub_unit_2=personnel.sub_unit_2,
        sub_unit_3=personnel.sub_unit_3,
        status=personnel.status,
        callup_status=personnel.callup_status,
        remarks=personnel.remarks,
        source=personnel.source,
        created_at=personnel.created_at,
        updated_at=personnel.updated_at,
        created_by=personnel.created_by,
        updated_by=personnel.updated_by,
    )


@router.get("/personnel/{personnel_id}", response_model=PersonnelResponse)
async def get_personnel(
    personnel_id: str,
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get personnel by ID (admin/super_admin only)."""
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can view personnel",
        )

    result = await db.execute(select(Personnel).where(Personnel.id == personnel_id))
    personnel = result.scalar_one_or_none()

    if not personnel:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Personnel not found",
        )

    return PersonnelResponse(
            id=personnel.id,
            nominal_roll_id=personnel.nominal_roll_id,
            pers_no=personnel.pers_no,
            rank=personnel.rank,
            category=personnel.category,
            name=personnel.full_name,
            unit=personnel.unit,
            sub_unit_1=personnel.sub_unit_1,
            sub_unit_2=personnel.sub_unit_2,
            sub_unit_3=personnel.sub_unit_3,
            status=personnel.status,
            callup_status=personnel.callup_status,
            remarks=personnel.remarks,
            source=personnel.source,
            created_at=personnel.created_at,
            updated_at=personnel.updated_at,
            created_by=personnel.created_by,
            updated_by=personnel.updated_by,
        )
@router.patch(
    "/personnel/{personnel_id}", response_model=PersonnelResponse
)
async def update_personnel(
    personnel_id: str,
    personnel_update: PersonnelUpdate,
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Update personnel information.

    Under the 1:1 tagging model the NominalRoll is read-only. Identity
    fields (``rank``, ``name``) are rejected with 409. Unit/subunit edits
    are recorded as a TaggingEntry overlay on the personnel's NR tagging.
    ``status`` is still applied directly to the personnel row. Response
    fields return the **effective** values (``to_*`` if tagged else
    canonical).
    """
    # Check permissions
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can update personnel records",
        )

    update_data = personnel_update.model_dump(exclude_unset=True)

    # Reject identity fields — the NR is read-only.
    identity_present = _IDENTITY_FIELDS & update_data.keys()
    if identity_present:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "NominalRoll is read-only; identity fields cannot be edited: "
                + ", ".join(sorted(identity_present))
            ),
        )

    # pers_no is the fill-in-later flow for manual adds: super-admin only.
    # Admins keep every other PATCH field (status / callup_status / remarks).
    pers_no_update_present = "pers_no" in update_data
    if pers_no_update_present and user_role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only super-admins can change personnel numbers",
        )

    # Partition the remaining update into remap (-> tagging) vs direct
    # personnel-column updates (status / callup_status / remarks).
    remap_updates = {
        field: value
        for field, value in update_data.items()
        if field in _REMAP_FIELDS
    }
    status_update = update_data.get("status")
    callup_status_update = update_data.get("callup_status")
    # Membership check (not `is not None`): an explicit null clears remarks.
    remarks_update_present = "remarks" in update_data

    result = await db.execute(
        select(Personnel).where(Personnel.id == personnel_id)
    )
    personnel = result.scalar_one_or_none()
    if not personnel:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Personnel not found",
        )

    # Apply status / callup_status / remarks directly to the personnel row
    # (still allowed). Changing callup_status away from "Called Up" only
    # hides the person from the attendance view — existing attendance
    # records are never touched.
    if status_update is not None:
        personnel.status = status_update
        personnel.updated_at = utc_dt.db_utcnow()
        personnel.updated_by = user_id
    if callup_status_update is not None:
        personnel.callup_status = callup_status_update
        personnel.updated_at = utc_dt.db_utcnow()
        personnel.updated_by = user_id
    if remarks_update_present:
        personnel.remarks = update_data.get("remarks")
        personnel.updated_at = utc_dt.db_utcnow()
        personnel.updated_by = user_id

    # pers_no fill-in-later: explicit null / empty clears (membership
    # semantics like remarks). Uniqueness is per-roll, excluding self.
    if pers_no_update_present:
        new_pers_no = update_data.get("pers_no")
        if new_pers_no is not None:
            duplicate = (
                await db.execute(
                    select(Personnel).where(
                        Personnel.nominal_roll_id == personnel.nominal_roll_id,
                        Personnel.pers_no == new_pers_no,
                        Personnel.id != personnel.id,
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail=(
                        f"Personnel with pers_no {new_pers_no} already exists "
                        "on this nominal roll"
                    ),
                )
        personnel.pers_no = new_pers_no
        personnel.updated_at = utc_dt.db_utcnow()
        personnel.updated_by = user_id

    # Redirect unit/subunit edits to the tagging entry overlay.
    await _redirect_remap_to_tagging_entry(db, personnel, remap_updates, user_id)

    try:
        await db.commit()
    except IntegrityError:
        # Race on the per-roll unique constraint despite the pre-check above.
        await db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"Personnel with pers_no {update_data.get('pers_no')} already "
                "exists on this nominal roll"
            ),
        )
    await db.refresh(personnel)

    # Compute effective values for the response.
    entry = await _load_effective_remap_for_personnel(db, personnel)

    return PersonnelResponse(
        id=personnel.id,
        nominal_roll_id=personnel.nominal_roll_id,
        pers_no=personnel.pers_no,
        rank=personnel.rank,
        category=personnel.category,
        name=personnel.full_name,
        unit=entry.to_unit if entry else personnel.unit,
        sub_unit_1=entry.to_sub_unit_1 if entry else personnel.sub_unit_1,
        sub_unit_2=entry.to_sub_unit_2 if entry else personnel.sub_unit_2,
        sub_unit_3=entry.to_sub_unit_3 if entry else personnel.sub_unit_3,
        status=personnel.status,
        callup_status=personnel.callup_status,
        remarks=personnel.remarks,
        source=personnel.source,
        created_at=personnel.created_at,
        updated_at=personnel.updated_at,
        created_by=personnel.created_by,
        updated_by=personnel.updated_by,
    )


@router.get(
    "/personnel/{personnel_id}/attendance-history",
    response_model=PersonnelAttendanceHistoryResponse,
)
async def get_personnel_attendance_history(
    personnel_id: str,
    nominal_roll_id: str | None = Query(
        None, description="Optional NR scope (must match the personnel's NR)"
    ),
    date_from: utc_dt.date | None = Query(
        None, description="Filter attendance from this date"
    ),
    date_to: utc_dt.date | None = Query(
        None, description="Filter attendance until this date"
    ),
    limit: int = Query(100, ge=1, le=1000, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """Get attendance history for a personnel member.

    Returns per-day AM/PM attendance with summary statistics. AM and PM slots
    are counted independently toward totals. Supports date range filtering and
    pagination.
    """
    # Resolve personnel (and its NR).
    personnel_result = await db.execute(
        select(Personnel).where(Personnel.id == personnel_id)
    )
    personnel = personnel_result.scalar_one_or_none()
    if not personnel:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Personnel not found",
        )

    resolved_nr = personnel.nominal_roll_id
    if nominal_roll_id and nominal_roll_id != resolved_nr:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Personnel does not belong to this nominal roll",
        )

    # Build attendance query (NR/Tagging-scoped, no sessions).
    query = select(Attendance).where(Attendance.personnel_id == personnel_id)
    if date_from:
        query = query.where(Attendance.date >= date_from)
    if date_to:
        query = query.where(Attendance.date <= date_to)

    # Total count (before pagination).
    count_subquery = query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_count = (await db.execute(count_query)).scalar() or 0

    query = query.offset(offset).limit(limit).order_by(Attendance.date.desc())

    result = await db.execute(query)
    records = list(result.scalars().all())

    # Build items + stats (AM and PM each count as one slot).
    attendance_items = []
    present_count = 0
    absent_count = 0

    for record in records:
        for slot_value in (record.status_am, record.status_pm):
            if slot_value in PRESENT_LIKE_STATUSES:
                present_count += 1
            else:
                absent_count += 1

        attendance_items.append(
            PersonnelAttendanceHistoryItem(
                id=record.id,
                nominal_roll_id=record.nominal_roll_id,
                date=record.date,
                status_am=record.status_am,
                remarks_am=record.remarks_am,
                status_pm=record.status_pm,
                remarks_pm=record.remarks_pm,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

    total_slots = present_count + absent_count
    attendance_rate = (present_count / total_slots * 100) if total_slots else 0.0

    stats = PersonnelAttendanceHistoryStats(
        total_slots=total_slots,
        present_count=present_count,
        absent_count=absent_count,
        attendance_rate=round(attendance_rate, 2),
    )

    return PersonnelAttendanceHistoryResponse(
        personnel_id=personnel_id,
        nominal_roll_id=resolved_nr,
        date_from=date_from,
        date_to=date_to,
        stats=stats,
        attendance_records=attendance_items,
        total_count=total_count,
    )
