"""Attendance management API endpoints.

Attendance is taken against the Nominal Roll that is currently **active for
attendance** (one row per person/day, AM and PM slots), always with the NR's
1:1 tagging overlay applied. Writes are only permitted against the active NR
(a super-admin marks an NR "Use for Attendance" on the nominal-rolls API).
Updates are upserts keyed on (personnel_id, date).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.api.subunit_access import (
    assert_can_update_attendance,
    get_assigned_subunit_1s,
    resolve_effective_subunit_1_map,
)
from parade_state.api.tagging import _load_nr_tagging
from parade_state.db import get_db_session
from parade_state.models import Attendance, NominalRoll, Personnel
from parade_state.models.attendance import ATTENDANCE_STATUSES, PRESENT_LIKE_STATUSES
from parade_state.models.schemas import (
    AttendanceBulkUpsert,
    AttendanceResponse,
    AttendanceUpsert,
    CopyRemarksResponse,
)
from parade_state.utils import utc_dt

router = APIRouter()


# ============================================================================
# Helpers
# ============================================================================


async def require_attendance_active(
    nominal_roll_id: str,
    db: AsyncSession,
) -> NominalRoll:
    """Load the NR; 400 unless it is the NR currently active for attendance."""
    nr = (
        await db.execute(
            select(NominalRoll).where(NominalRoll.id == nominal_roll_id)
        )
    ).scalar_one_or_none()
    if nr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {nominal_roll_id}",
        )
    if not nr.attendance_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Attendance is not active for this nominal roll. "
                "A super-admin must mark it 'Use for Attendance' first."
            ),
        )
    return nr


async def applied_tagging_id(db: AsyncSession, nr: NominalRoll) -> str | None:
    """The id of the NR's 1:1 tagging (the overlay always applied)."""
    tagging = await _load_nr_tagging(db, str(nr.id), with_entries=False)
    return str(tagging.id) if tagging else None


def is_retroactive(target_date: utc_dt.date) -> bool:
    """True if the target date is before today (UTC)."""
    return target_date < utc_dt.utcnow().date()


async def get_roster_for_scope(
    nominal_roll_id: str,
    db: AsyncSession,
) -> list[Personnel]:
    """Active, Called Up personnel on an NR (the attendance roster).

    Non-Called-Up callup statuses are hidden; their attendance records
    (if any) are preserved untouched.
    """
    result = await db.execute(
        select(Personnel).where(
            Personnel.nominal_roll_id == nominal_roll_id,
            Personnel.status == "active",
            Personnel.callup_status == "Called Up",
        )
    )
    return list(result.scalars().all())


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

    Enforces that the NR is the one currently active for attendance AND that
    the caller has Subunit-1 assignment for each target personnel's effective
    sub_unit_1 (403 otherwise; super_admin bypasses). Each entry is keyed on
    (personnel_id, date); existing rows are updated, new rows are created with
    snapshot data from Personnel.
    """
    nr = await require_attendance_active(payload.nominal_roll_id, db)
    tagging_id = await applied_tagging_id(db, nr)

    # Subunit-1 access enforcement (issue #4 PR 2).
    personnel_ids = [r.personnel_id for r in payload.records]
    await assert_can_update_attendance(
        db,
        payload.nominal_roll_id,
        user_id,
        user_role,
        personnel_ids,
        tagging_id,
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
    nr = await require_attendance_active(nominal_roll_id, db)
    tagging_id = await applied_tagging_id(db, nr)

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
            db, nominal_roll_id, user_id, user_role, tagging_id, all_pids
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
        db, nominal_roll_id, user_id, user_role, tagging_id, all_pids
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
# Aggregate stats helper (reused by grouping summary view)
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
