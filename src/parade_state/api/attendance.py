"""Attendance management API endpoints.

Attendance is taken against an NR/Tagging scope (one row per person/day, AM and
PM slots). Before any write, the NR must have an active ``AttendanceScope``
(super-admin activates it). Updates are upserts keyed on (personnel_id, date).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import Attendance, AttendanceScope, Personnel
from parade_state.api.subunit_access import (
    assert_can_update_attendance,
    get_assigned_subunit_1s,
    resolve_effective_subunit_1_map,
)
from parade_state.models.attendance import ATTENDANCE_STATUSES, PRESENT_LIKE_STATUSES
from parade_state.models.schemas import (
    AttendanceBulkUpsert,
    AttendanceResponse,
    AttendanceScopeActivate,
    AttendanceScopeResponse,
    AttendanceUpsert,
    CopyRemarksResponse,
)
from parade_state.utils import utc_dt

router = APIRouter()


# ============================================================================
# Helpers
# ============================================================================


async def get_active_scope(
    nominal_roll_id: str,
    db: AsyncSession,
) -> AttendanceScope | None:
    """Return the active attendance scope for an NR, or None."""
    result = await db.execute(
        select(AttendanceScope).where(
            AttendanceScope.nominal_roll_id == nominal_roll_id
        )
    )
    return result.scalar_one_or_none()


async def require_active_scope(
    nominal_roll_id: str,
    db: AsyncSession,
) -> AttendanceScope:
    """Return the active scope for an NR, 400 if attendance not activated."""
    scope = await get_active_scope(nominal_roll_id, db)
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attendance is not activated for this nominal roll. "
            "A super-admin must activate a scope first.",
        )
    return scope


def is_retroactive(target_date: utc_dt.date) -> bool:
    """True if the target date is before today (UTC)."""
    return target_date < utc_dt.utcnow().date()


async def get_roster_for_scope(
    nominal_roll_id: str,
    db: AsyncSession,
) -> list[Personnel]:
    """Active personnel on an NR (the attendance roster)."""
    result = await db.execute(
        select(Personnel).where(
            Personnel.nominal_roll_id == nominal_roll_id,
            Personnel.status == "active",
        )
    )
    return list(result.scalars().all())


# ============================================================================
# Attendance scope endpoints
# ============================================================================


@router.put(
    "/scope/{nominal_roll_id}",
    response_model=AttendanceScopeResponse,
)
async def activate_attendance_scope(
    nominal_roll_id: str,
    payload: AttendanceScopeActivate,
    user_id: str = Query(..., description="User ID activating the scope"),
    user_role: str = Query(..., description="User role"),
    db: AsyncSession = Depends(get_db_session),
):
    """Activate (or change) the attendance scope for a nominal roll.

    Super-admin only. ``tagging_id`` omitted/None → the NR itself is the scope;
    otherwise the given Tagging overlay is. The tagging must belong to this NR.
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super-admins can activate the attendance scope",
        )

    # Validate tagging belongs to this NR if provided.
    if payload.tagging_id:
        from parade_state.models import Tagging

        tagging_result = await db.execute(
            select(Tagging).where(Tagging.id == payload.tagging_id)
        )
        tagging = tagging_result.scalar_one_or_none()
        if not tagging or tagging.nominal_roll_id != nominal_roll_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tagging does not belong to this nominal roll",
            )

    existing = await get_active_scope(nominal_roll_id, db)
    if existing is None:
        existing = AttendanceScope(
            nominal_roll_id=nominal_roll_id,
            tagging_id=payload.tagging_id,
            activated_by=user_id,
        )
        db.add(existing)
    else:
        existing.tagging_id = payload.tagging_id
        existing.activated_by = user_id
        existing.activated_at = utc_dt.ensure_naive(utc_dt.utcnow())

    try:
        await db.commit()
        await db.refresh(existing)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to activate attendance scope",
        ) from None

    return existing


@router.get(
    "/scope/{nominal_roll_id}",
    response_model=AttendanceScopeResponse | None,
)
async def get_scope(
    nominal_roll_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Return the active scope for an NR (null if not activated)."""
    scope = await get_active_scope(nominal_roll_id, db)
    return scope


# ============================================================================
# Attendance read/upsert endpoints
# ============================================================================


@router.get("/", response_model=list[AttendanceResponse])
async def list_attendance(
    nominal_roll_id: str = Query(..., description="NR to list attendance for"),
    date: utc_dt.date = Query(..., description="Attendance date"),
    db: AsyncSession = Depends(get_db_session),
):
    """List attendance rows for an NR on a given date.

    Returns the active roster joined to any existing attendance rows; personnel
    without an attendance row for that date are not included (callers should
    synthesize default rows from the roster when needed).
    """
    result = await db.execute(
        select(Attendance).where(
            and_(
                Attendance.nominal_roll_id == nominal_roll_id,
                Attendance.date == date,
            )
        )
    )
    return list(result.scalars().all())


@router.put("/upsert", response_model=list[AttendanceResponse])
async def bulk_upsert_attendance(
    payload: AttendanceBulkUpsert,
    user_id: str = Query(..., description="User ID recording attendance"),
    user_role: str = Query(..., description="User role"),
    db: AsyncSession = Depends(get_db_session),
):
    """Bulk upsert attendance rows for an NR's roster.

    Enforces that the NR's attendance scope is active AND that the caller has
    Subunit-1 assignment for each target personnel's effective sub_unit_1
    (403 otherwise; super_admin bypasses). Each entry is keyed on
    (personnel_id, date); existing rows are updated, new rows are created with
    snapshot data from Personnel.
    """
    scope = await require_active_scope(payload.nominal_roll_id, db)

    # Subunit-1 access enforcement (issue #4 PR 2).
    personnel_ids = [r.personnel_id for r in payload.records]
    await assert_can_update_attendance(
        db,
        payload.nominal_roll_id,
        user_id,
        user_role,
        personnel_ids,
        scope.tagging_id,
    )

    # Index existing rows for this NR + date set.
    dates = {r.date for r in payload.records}
    existing_result = await db.execute(
        select(Attendance).where(
            and_(
                Attendance.nominal_roll_id == payload.nominal_roll_id,
                Attendance.date.in_(dates),
            )
        )
    )
    existing_by_key: dict[tuple[str, utc_dt.date], Attendance] = {
        (r.personnel_id, r.date): r for r in existing_result.scalars().all()
    }

    # Preload personnel snapshots for any new rows.
    personnel_result = await db.execute(
        select(Personnel).where(Personnel.id.in_(personnel_ids))
    )
    personnel_by_id = {str(p.id): p for p in personnel_result.scalars().all()}

    touched: list[Attendance] = []
    now = utc_dt.ensure_naive(utc_dt.utcnow())

    for entry in payload.records:
        person = personnel_by_id.get(entry.personnel_id)
        if person is None or person.nominal_roll_id != payload.nominal_roll_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Personnel {entry.personnel_id} is not on this nominal roll",
            )

        key = (entry.personnel_id, entry.date)
        record = existing_by_key.get(key)
        retroactive = is_retroactive(entry.date)

        if record is None:
            record = Attendance(
                personnel_id=entry.personnel_id,
                nominal_roll_id=payload.nominal_roll_id,
                tagging_id=scope.tagging_id,
                date=entry.date,
                status_am=entry.status_am,
                remarks_am=entry.remarks_am,
                status_pm=entry.status_pm,
                remarks_pm=entry.remarks_pm,
                notes_snapshot=None,
                unit_snapshot=person.unit,
                sub_unit_1_snapshot=person.sub_unit_1,
                sub_unit_2_snapshot=person.sub_unit_2,
                sub_unit_3_snapshot=person.sub_unit_3,
                created_by=user_id,
                updated_by=user_id,
                last_edit_at=now,
                last_edit_by=user_id,
                is_retroactive_edit=retroactive,
            )
            db.add(record)
            existing_by_key[key] = record
        else:
            record.status_am = entry.status_am
            record.remarks_am = entry.remarks_am
            record.status_pm = entry.status_pm
            record.remarks_pm = entry.remarks_pm
            record.updated_by = user_id
            record.updated_at = now
            record.last_edit_at = now
            record.last_edit_by = user_id
            if retroactive:
                record.is_retroactive_edit = True

        touched.append(record)

    try:
        await db.commit()
        for record in touched:
            await db.refresh(record)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Attendance upsert failed (constraint violation)",
        ) from None

    return touched


# ============================================================================
# Copy Remarks endpoint
# ============================================================================
#
# Per issue Q3: before 12pm → copy previous day's remarks_pm into today's
# remarks_am. After 12pm → copy today's remarks_am into remarks_pm. On the
# NR's first day of attendance there is no prior day, so the AM copy is a
# no-op (the UI disables the button).


@router.post("/copy-remarks", response_model=CopyRemarksResponse)
async def copy_remarks(
    nominal_roll_id: str = Query(..., description="NR to copy remarks for"),
    date: utc_dt.date = Query(..., description="Target attendance date"),
    user_id: str = Query(..., description="User ID triggering the copy"),
    user_role: str = Query(..., description="User role"),
    db: AsyncSession = Depends(get_db_session),
):
    """Copy remarks across AM/PM slots.

    - Before 12pm (local): copy previous day's ``remarks_pm`` into today's
      ``remarks_am`` for each personnel row.
    - After 12pm: copy today's ``remarks_am`` into today's ``remarks_pm``.

    Rows with an empty source remark are skipped. Only personnel whose
    effective sub_unit_1 the caller is assigned to are affected (super_admin
    bypasses; deny-by-default: no assignments → 403). Returns counts.
    """
    scope = await require_active_scope(nominal_roll_id, db)

    # Resolve accessible personnel set (Subunit-1 enforcement).
    now = utc_dt.utcnow()
    slot: str = "am" if now.hour < 12 else "pm"

    if slot == "am":
        source_date = date - utc_dt.timedelta(days=1)
        rows = await db.execute(
            select(Attendance).where(
                and_(
                    Attendance.nominal_roll_id == nominal_roll_id,
                    Attendance.date.in_([source_date, date]),
                )
            )
        )
        by_key: dict[tuple[str, utc_dt.date], Attendance] = {
            (r.personnel_id, r.date): r for r in rows.scalars().all()
        }

        all_pids = {k[0] for k in by_key.keys()}
        accessible_pids = await _accessible_pids(
            db, nominal_roll_id, user_id, user_role, scope.tagging_id, all_pids
        )

        updated = 0
        skipped = 0
        now_naive = utc_dt.ensure_naive(utc_dt.utcnow())
        # Iterate personnel that have a today row OR a prior-day row.
        for pid in all_pids:
            if pid not in accessible_pids:
                continue  # not assigned to this person's subunit_1
            source = by_key.get((pid, source_date))
            target = by_key.get((pid, date))
            source_remark = source.remarks_pm if source else None

            if not source_remark:
                skipped += 1
                continue

            if target is None:
                person_result = await db.execute(
                    select(Personnel).where(Personnel.id == pid)
                )
                person = person_result.scalar_one_or_none()
                if person is None or person.nominal_roll_id != nominal_roll_id:
                    skipped += 1
                    continue
                target = Attendance(
                    personnel_id=pid,
                    nominal_roll_id=nominal_roll_id,
                    tagging_id=None,
                    date=date,
                    status_am="absent",
                    status_pm="absent",
                    unit_snapshot=person.unit,
                    sub_unit_1_snapshot=person.sub_unit_1,
                    sub_unit_2_snapshot=person.sub_unit_2,
                    sub_unit_3_snapshot=person.sub_unit_3,
                    created_by=user_id,
                    updated_by=user_id,
                    last_edit_at=now_naive,
                    last_edit_by=user_id,
                )
                db.add(target)

            target.remarks_am = source_remark
            target.updated_by = user_id
            target.updated_at = now_naive
            target.last_edit_at = now_naive
            target.last_edit_by = user_id
            updated += 1

        await db.commit()
        return CopyRemarksResponse(
            nominal_roll_id=nominal_roll_id,
            date=date,
            slot="am",
            updated=updated,
            skipped=skipped,
        )

    # slot == "pm": copy today's remarks_am into remarks_pm.
    rows = await db.execute(
        select(Attendance).where(
            and_(
                Attendance.nominal_roll_id == nominal_roll_id,
                Attendance.date == date,
            )
        )
    )
    all_rows = list(rows.scalars().all())
    all_pids = {r.personnel_id for r in all_rows}
    accessible_pids = await _accessible_pids(
        db, nominal_roll_id, user_id, user_role, scope.tagging_id, all_pids
    )

    now_naive = utc_dt.ensure_naive(utc_dt.utcnow())
    updated = 0
    skipped = 0
    for target in all_rows:
        if target.personnel_id not in accessible_pids:
            continue  # not assigned to this person's subunit_1
        if not target.remarks_am:
            skipped += 1
            continue
        target.remarks_pm = target.remarks_am
        target.updated_by = user_id
        target.updated_at = now_naive
        target.last_edit_at = now_naive
        target.last_edit_by = user_id
        updated += 1

    await db.commit()
    return CopyRemarksResponse(
        nominal_roll_id=nominal_roll_id,
        date=date,
        slot="pm",
        updated=updated,
        skipped=skipped,
    )


async def _accessible_pids(
    db: AsyncSession,
    nominal_roll_id: str,
    user_id: str,
    user_role: str,
    active_tagging_id: str | None,
    all_pids: set[str],
) -> set[str]:
    """Resolve which of ``all_pids`` the user may write to (Subunit-1 rule).

    super_admin → all of them. Otherwise, require at least one assignment on
    the NR (deny-by-default: 403 if none) and return the subset whose
    effective sub_unit_1 is assigned.
    """
    if user_role == "super_admin":
        return set(all_pids)

    allowed = await get_assigned_subunit_1s(db, user_id, nominal_roll_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "No Subunit-1 assignments on this nominal roll. "
                "Ask a super-admin to grant access."
            ),
        )
    if not all_pids:
        return set()
    eff_map = await resolve_effective_subunit_1_map(
        db, list(all_pids), active_tagging_id
    )
    return {pid for pid in all_pids if eff_map.get(pid) in allowed}


# ============================================================================
# Aggregate stats helper (reused by deployment summary view)
# ============================================================================


async def attendance_counts_for_date(
    nominal_roll_id: str,
    date: utc_dt.date,
    db: AsyncSession,
) -> dict[str, dict[str, int]]:
    """Return AM/PM present/absent/total counts for an NR on a date.

    Shape: ``{"am": {"present": n, "absent": n, "total": n}, "pm": {...}}``.
    """
    result = await db.execute(
        select(Attendance).where(
            and_(
                Attendance.nominal_roll_id == nominal_roll_id,
                Attendance.date == date,
            )
        )
    )
    rows = list(result.scalars().all())

    counts = {"am": {"present": 0, "absent": 0, "total": 0},
              "pm": {"present": 0, "absent": 0, "total": 0}}
    for row in rows:
        for slot in ("am", "pm"):
            value = row.status_am if slot == "am" else row.status_pm
            counts[slot]["total"] += 1
            if value in PRESENT_LIKE_STATUSES:
                counts[slot]["present"] += 1
            else:
                counts[slot]["absent"] += 1
    return counts
