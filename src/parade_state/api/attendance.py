"""Attendance management API endpoints.

Attendance is taken against the Nominal Roll that is currently **active for
attendance** (one row per person/day, AM and PM slots), always with the NR's
1:1 tagging overlay applied. Writes are only permitted against the active NR
(a super-admin marks an NR "Use for Attendance" on the nominal-rolls API).
Updates are upserts keyed on (personnel_id, date).
"""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.api.subunit_access import (
    assert_locations_in_scope,
    assert_nr_accessible,
    in_scope_pids,
)
from parade_state.api.tagging import _load_nr_tagging
from parade_state.db import get_db_session
from parade_state.models import Attendance, NominalRoll, Personnel, TaggingEntry
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
    user_id: str = Query(..., description="User ID for authorization"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
):
    """List attendance rows for an NR on a given date.

    Personnel without an attendance row for that date are not included
    (callers should synthesize default rows from the roster when needed).
    Read scoping (issue #28): ``super_admin`` sees every row; everyone
    else only rows whose personnel fall inside their (unit, sub_unit_1)
    scope — deny-by-default, 403 with no grants on the NR.
    """
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
    tagging_id = await applied_tagging_id(db, nr)

    await assert_nr_accessible(db, user_id, user_role, nominal_roll_id)

    result = await db.execute(
        select(Attendance).where(
            and_(
                Attendance.nominal_roll_id == nominal_roll_id,
                Attendance.date == date,
            )
        )
    )
    rows = list(result.scalars().all())

    accessible = await in_scope_pids(
        db,
        user_id,
        user_role,
        nominal_roll_id,
        tagging_id,
        {row.personnel_id for row in rows},
    )
    if accessible is not None:
        rows = [row for row in rows if row.personnel_id in accessible]
    return rows


@router.put("/upsert", response_model=list[AttendanceResponse])
async def bulk_upsert_attendance(
    payload: AttendanceBulkUpsert,
    user_id: str = Query(..., description="User ID recording attendance"),
    user_role: str = Query(..., description="User role"),
    db: AsyncSession = Depends(get_db_session),
):
    """Bulk upsert attendance rows for an NR's roster.

    Enforces that the NR is the one currently active for attendance AND that
    the caller's scope covers each target personnel's effective
    (unit, sub_unit_1) (403 otherwise; super_admin bypasses). Each entry is
    keyed on (personnel_id, date); existing rows are updated, new rows are
    created with snapshot data from Personnel.
    """
    nr = await require_attendance_active(payload.nominal_roll_id, db)
    tagging_id = await applied_tagging_id(db, nr)

    # Scope enforcement (issues #4 and #28): effective (unit, sub_unit_1)
    # must be covered by a grant; super_admin bypasses.
    personnel_ids = [r.personnel_id for r in payload.records]
    await assert_locations_in_scope(
        db,
        user_id,
        user_role,
        payload.nominal_roll_id,
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
# Issue 20: the caller names the source (date + slot) and destination
# (date + slot) explicitly — the old time-of-day inference lives on only as
# the modal's prefill defaults. Blank source remarks leave the destination
# untouched; destination rows are created on demand.


@router.post("/copy-remarks", response_model=CopyRemarksResponse)
async def copy_remarks(
    nominal_roll_id: str = Query(..., description="NR to copy remarks for"),
    source_date: utc_dt.date = Query(..., description="Copy-from date"),
    source_slot: str = Query(..., description="Copy-from slot: am or pm"),
    dest_date: utc_dt.date = Query(..., description="Copy-to date"),
    dest_slot: str = Query(..., description="Copy-to slot: am or pm"),
    sub_unit_1: str | None = Query(
        None,
        description=(
            "Restrict the copy to personnel whose effective sub_unit_1 "
            "matches (the attendance page's filter)"
        ),
    ),
    user_id: str = Query(..., description="User ID triggering the copy"),
    user_role: str = Query(..., description="User role"),
    db: AsyncSession = Depends(get_db_session),
):
    """Copy remarks from one (date, slot) to another for the scoped roster.

    Scope: the active Called Up roster, optionally narrowed to an effective
    sub_unit_1 (the page's view filter), intersected with the caller's
    (unit, sub_unit_1) write scope (super_admin bypasses;
    deny-by-default: no grants → 403). Rows with an empty source remark are
    skipped (the
    destination keeps its remark); missing destination rows are created
    (statuses default to absent). Source and destination must differ.
    """
    if source_slot not in ("am", "pm") or dest_slot not in ("am", "pm"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_slot and dest_slot must each be 'am' or 'pm'",
        )
    if source_date == dest_date and source_slot == dest_slot:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and destination must differ",
        )

    nr = await require_attendance_active(nominal_roll_id, db)
    tagging_id = await applied_tagging_id(db, nr)

    roster = await get_roster_for_scope(nominal_roll_id, db)

    # Optional view filter: effective sub_unit_1 (tagging-aware).
    if sub_unit_1:
        entry_rows = (
            await db.execute(
                select(TaggingEntry).where(TaggingEntry.tagging_id == tagging_id)
            )
        ).scalars().all() if tagging_id else []
        to_sub1 = {str(e.personnel_id): e.to_sub_unit_1 for e in entry_rows}
        roster = [
            p
            for p in roster
            if (to_sub1.get(str(p.id), p.sub_unit_1) == sub_unit_1)
        ]

    # Write access (scope rule): super_admin → all; deny-by-default.
    all_pids = {str(p.id) for p in roster}
    await assert_nr_accessible(db, user_id, user_role, nominal_roll_id)
    accessible_pids = await in_scope_pids(
        db, user_id, user_role, nominal_roll_id, tagging_id, all_pids
    )

    rows = (
        await db.execute(
            select(Attendance).where(
                and_(
                    Attendance.nominal_roll_id == nominal_roll_id,
                    Attendance.date.in_([source_date, dest_date]),
                )
            )
        )
    ).scalars().all()
    by_key: dict[tuple[str, utc_dt.date], Attendance] = {
        (r.personnel_id, r.date): r for r in rows
    }

    now_naive = utc_dt.ensure_naive(utc_dt.utcnow())
    updated = 0
    skipped = 0
    for pid in all_pids:
        if accessible_pids is not None and pid not in accessible_pids:
            continue  # outside the caller's effective (unit, sub_unit_1) scope
        source = by_key.get((pid, source_date))
        source_remark = (
            source.remarks_pm if source_slot == "pm" else source.remarks_am
        ) if source else None

        if not source_remark:
            skipped += 1  # blank/missing source → leave destination untouched
            continue

        target = by_key.get((pid, dest_date))
        if target is None:
            person = next(
                (p for p in roster if str(p.id) == pid), None
            )
            if person is None:
                skipped += 1
                continue
            target = Attendance(
                personnel_id=pid,
                nominal_roll_id=nominal_roll_id,
                date=dest_date,
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
                is_retroactive_edit=is_retroactive(dest_date),
            )
            db.add(target)
            by_key[(pid, dest_date)] = target

        if dest_slot == "pm":
            target.remarks_pm = source_remark
        else:
            target.remarks_am = source_remark
        target.updated_by = user_id
        target.updated_at = now_naive
        target.last_edit_at = now_naive
        target.last_edit_by = user_id
        updated += 1

    await db.commit()
    return CopyRemarksResponse(
        nominal_roll_id=nominal_roll_id,
        source_date=source_date,
        source_slot=source_slot,
        dest_date=dest_date,
        dest_slot=dest_slot,
        updated=updated,
        skipped=skipped,
    )


# ============================================================================
# CSV Export
# ============================================================================


# Display labels for the exported statuses — exactly the option labels the
# attendance page renders (raw enum values like "yet_to_inpro" would be
# unreadable in a spreadsheet).
_STATUS_LABELS = {
    "present": "Present",
    "absent": "Absent",
    "time_off": "Time Off",
    "mc": "MC",
    "yet_to_inpro": "Yet to Inpro",
    "outpro": "Outpro",
    "reporting_sick": "Reporting Sick",
    "late": "Late",
    "att_out": "Att Out",
}


@router.get("/export")
async def export_attendance_csv(
    nominal_roll_id: str = Query(..., description="NR to export attendance for"),
    date: utc_dt.date = Query(..., description="Attendance date"),
    sub_unit_1: str | None = Query(
        None,
        description=(
            "Restrict the export to personnel whose effective sub_unit_1 "
            "matches (the attendance page's filter)"
        ),
    ),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role"),
    db: AsyncSession = Depends(get_db_session),
):
    """Export the attendance marking table exactly as displayed.

    Columns: Unit, Sub-unit 1-3, Category, Rank, Name, AM/PM Status and
    Remarks. The roster is the active Called Up personnel with the NR's 1:1
    tagging overlay applied, ordered like the marking page; personnel
    without an attendance row for the date export as Absent (the page's
    default). Read scoping mirrors the page: super_admin exports the whole
    roster, everyone else only rows inside their (unit, sub_unit_1) scope
    (403 with no grants on the NR).
    """
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
    tagging_id = await applied_tagging_id(db, nr)

    roster = (
        (
            await db.execute(
                select(Personnel)
                .where(
                    Personnel.nominal_roll_id == nominal_roll_id,
                    Personnel.status == "active",
                    Personnel.callup_status == "Called Up",
                )
                .order_by(
                    Personnel.unit,
                    Personnel.sub_unit_1,
                    Personnel.sub_unit_2,
                    Personnel.sub_unit_3,
                    Personnel.rank,
                    Personnel.full_name,
                )
            )
        )
        .scalars()
        .all()
    )

    entry_by_person: dict[str, TaggingEntry] = {}
    if tagging_id:
        entries = (
            await db.execute(
                select(TaggingEntry).where(
                    TaggingEntry.tagging_id == tagging_id
                )
            )
        ).scalars().all()
        entry_by_person = {str(e.personnel_id): e for e in entries}

    # Optional view filter: effective sub_unit_1 (tagging-aware).
    if sub_unit_1:
        to_sub1 = {
            pid: (e.to_sub_unit_1 if e else None)
            for pid, e in entry_by_person.items()
        }
        roster = [
            p
            for p in roster
            if to_sub1.get(str(p.id), p.sub_unit_1) == sub_unit_1
        ]

    # Read scoping (scope rule, same deny-by-default as writes).
    await assert_nr_accessible(db, user_id, user_role, nominal_roll_id)
    accessible_pids = await in_scope_pids(
        db,
        user_id,
        user_role,
        nominal_roll_id,
        tagging_id,
        {str(p.id) for p in roster},
    )

    att_by_person = {
        a.personnel_id: a
        for a in (
            await db.execute(
                select(Attendance).where(
                    and_(
                        Attendance.nominal_roll_id == nominal_roll_id,
                        Attendance.date == date,
                    )
                )
            )
        ).scalars().all()
    }

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Unit", "Sub-unit 1", "Sub-unit 2", "Sub-unit 3", "Category",
            "Rank", "Name", "AM Status", "AM Remarks", "PM Status",
            "PM Remarks",
        ]
    )
    for person in roster:
        if accessible_pids is not None and str(person.id) not in accessible_pids:
            continue
        record = att_by_person.get(str(person.id))
        entry = entry_by_person.get(str(person.id))
        writer.writerow(
            [
                entry.to_unit if entry else person.unit,
                (entry.to_sub_unit_1 if entry else person.sub_unit_1) or "",
                (entry.to_sub_unit_2 if entry else person.sub_unit_2) or "",
                (entry.to_sub_unit_3 if entry else person.sub_unit_3) or "",
                person.category,
                person.rank,
                person.full_name,
                _STATUS_LABELS.get(
                    record.status_am if record else "absent", "absent"
                ),
                record.remarks_am if record and record.remarks_am else "",
                _STATUS_LABELS.get(
                    record.status_pm if record else "absent", "absent"
                ),
                record.remarks_pm if record and record.remarks_pm else "",
            ]
        )

    csv_data = output.getvalue()
    output.close()
    filename = (
        f"attendance_{date.strftime('%Y%m%d')}_"
        f"{utc_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    return StreamingResponse(
        io.BytesIO(csv_data.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================================================
# Aggregate stats helper (reused by the attendance web view)
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
