"""Nominal Roll API endpoints."""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import (
    AuditLog,
    CsvUpload,
    Grouping,
    NominalRoll,
    Personnel,
    TaggingEntry,
)
from parade_state.models.schemas import (
    NominalRollListItem,
    NominalRollResponse,
    NominalRollUpdate,
)
from parade_state.utils import utc_dt

router = APIRouter()


@router.get("", response_model=list[NominalRollListItem])
async def list_nominal_rolls(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> list[NominalRollListItem]:
    """List nominal rolls with their latest linked CsvUpload's filename.

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    # Subquery: most recent CsvUpload per nominal roll (by uploaded_at).
    latest_upload = (
        select(
            CsvUpload.nominal_roll_id.label("nominal_roll_id"),
            CsvUpload.original_filename.label("original_filename"),
        )
        .where(CsvUpload.nominal_roll_id.is_not(None))
        .order_by(CsvUpload.nominal_roll_id, CsvUpload.uploaded_at.desc())
        .subquery()
    )

    query = (
        select(
            NominalRoll.id,
            NominalRoll.caa,
            NominalRoll.attendance_active,
            NominalRoll.personnel_count,
            NominalRoll.uploaded_at,
            NominalRoll.uploaded_by,
            NominalRoll.csv_hash,
            NominalRoll.label,
            NominalRoll.remarks,
            latest_upload.c.original_filename,
        )
        .outerjoin(
            latest_upload, latest_upload.c.nominal_roll_id == NominalRoll.id
        )
        .order_by(NominalRoll.uploaded_at.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = (await db.execute(query)).all()

    return [
        NominalRollListItem(
            id=row.id,
            caa=row.caa,
            attendance_active=row.attendance_active,
            personnel_count=row.personnel_count,
            uploaded_at=row.uploaded_at,
            uploaded_by=row.uploaded_by,
            csv_hash=row.csv_hash,
            original_filename=row.original_filename,
            label=row.label,
            remarks=row.remarks,
        )
        for row in rows
    ]


@router.get("/{nominal_roll_id}", response_model=NominalRollResponse)
async def get_nominal_roll(
    nominal_roll_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> NominalRollResponse:
    """Fetch a single nominal roll by id with its latest CsvUpload's filename.

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    row = await _load_nominal_roll_with_filename(db, nominal_roll_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {nominal_roll_id}",
        )

    return _row_to_response(row)


@router.patch("/{nominal_roll_id}", response_model=NominalRollResponse)
async def update_nominal_roll(
    nominal_roll_id: str,
    update_data: NominalRollUpdate,
    user_id: str = Query(..., description="User ID making the update"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> NominalRollResponse:
    """Update a nominal roll (notes, label, remarks).

    Requires admin or super_admin role.
    """
    _require_admin(user_role)

    result = await db.execute(
        select(NominalRoll).where(NominalRoll.id == nominal_roll_id)
    )
    nominal_roll = result.scalar_one_or_none()
    if not nominal_roll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {nominal_roll_id}",
        )

    if update_data.notes is not None:
        nominal_roll.notes = update_data.notes

    if update_data.label is not None:
        nominal_roll.label = update_data.label

    if update_data.remarks is not None:
        nominal_roll.remarks = update_data.remarks

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Label already in use by another nominal roll.",
        ) from None

    row = await _load_nominal_roll_with_filename(db, nominal_roll_id)
    return _row_to_response(row)


@router.delete("/{nominal_roll_id}")
async def delete_nominal_roll(
    nominal_roll_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a nominal roll and cascade-delete all dependent data.

    Requires super_admin role. Cascades to personnel, attendance records,
    tagging, and related data. Groupings do NOT cascade — their FK is
    RESTRICT (issue 26) — so a roll with groupings based on it must have
    them deleted first.
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can delete nominal rolls",
        )

    result = await db.execute(
        select(NominalRoll).where(NominalRoll.id == nominal_roll_id)
    )
    nominal_roll = result.scalar_one_or_none()
    if not nominal_roll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Nominal roll not found: {nominal_roll_id}",
        )

    grouping_count = await db.scalar(
        select(func.count()).select_from(Grouping).where(
            Grouping.nominal_roll_id == nominal_roll_id
        )
    )
    if grouping_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{grouping_count} grouping(s) are based on this nominal roll. "
                "Delete them first — groupings are preserved when a roll is "
                "not in use."
            ),
        )

    await db.delete(nominal_roll)
    await db.commit()

    return {"detail": f"Nominal roll {nominal_roll_id} deleted"}


@router.post("/{nominal_roll_id}/activate-attendance", response_model=NominalRollResponse)
async def activate_attendance(
    nominal_roll_id: str,
    user_id: str = Query(..., description="User ID activating attendance"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> NominalRollResponse:
    """Mark this NR as the one active for attendance (auto-switch).

    Super-admin only. Deactivates any other currently-active NR in the same
    action, then marks this NR active with an audit stamp. Attendance writes
    are only permitted against the active NR (with its tagging applied).
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can activate attendance",
        )

    nr = await _load_nominal_roll_or_404(db, nominal_roll_id)

    # Auto-switch: deactivate every other active NR.
    others = (
        await db.execute(
            select(NominalRoll).where(
                NominalRoll.attendance_active.is_(True),
                NominalRoll.id != nr.id,
            )
        )
    ).scalars().all()
    for other in others:
        other.attendance_active = False

    nr.attendance_active = True
    nr.attendance_activated_at = utc_dt.ensure_naive(utc_dt.utcnow())
    nr.attendance_activated_by = user_id

    db.add(
        AuditLog(
            user_id=user_id,
            entity_type="nominal_roll",
            entity_id=str(nr.id),
            action="update",
            description=(
                f"Marked nominal roll CAA {nr.caa.isoformat()} "
                "active for attendance."
            ),
        )
    )

    await db.commit()

    row = await _load_nominal_roll_with_filename(db, nominal_roll_id)
    return _row_to_response(row)


@router.post("/{nominal_roll_id}/deactivate-attendance", response_model=NominalRollResponse)
async def deactivate_attendance(
    nominal_roll_id: str,
    user_id: str = Query(..., description="User ID deactivating attendance"),
    user_role: str = Query(..., description="User role for authorization"),
    db: AsyncSession = Depends(get_db_session),
) -> NominalRollResponse:
    """Deactivate attendance for this NR (leaves attendance inactive).

    Super-admin only. Clears ``attendance_active``; the activation audit
    stamp is kept as history. With no active NR, the attendance view shows
    an inactive message and writes are refused.
    """
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can deactivate attendance",
        )

    nr = await _load_nominal_roll_or_404(db, nominal_roll_id)

    if nr.attendance_active:
        nr.attendance_active = False
        db.add(
            AuditLog(
                user_id=user_id,
                entity_type="nominal_roll",
                entity_id=str(nr.id),
                action="update",
                description=(
                    f"Deactivated attendance for nominal roll "
                    f"CAA {nr.caa.isoformat()}."
                ),
            )
        )
        await db.commit()

    row = await _load_nominal_roll_with_filename(db, nominal_roll_id)
    return _row_to_response(row)


# ============================================================================
# CSV Export
# ============================================================================


@router.get("/{nominal_roll_id}/export")
async def export_nominal_roll_csv(
    nominal_roll_id: str,
    user_id: str = Query(..., description="User ID making the request"),
    user_role: str = Query(..., description="User role for authorization"),
    search: str | None = Query(None, description="Filter: name / pers no text search"),
    unit: str | None = Query(None, description="Filter: unit"),
    sub_unit_1: str | None = Query(None, description="Filter: sub-unit 1"),
    sub_unit_2: str | None = Query(None, description="Filter: sub-unit 2"),
    category: str | None = Query(None, description="Filter: category (Officer / WOSE)"),
    rank: str | None = Query(None, description="Filter: rank"),
    db: AsyncSession = Depends(get_db_session),
):
    """Export the nominal roll browser table exactly as displayed.

    Columns: Unit, Sub Unit 1-3, Category, Rank, Full Name, Pers No, Callup,
    Remarks — with the roll's 1:1 tagging overlay applied, ordered like the
    browser view. The view's filters (search, unit, sub-units, category,
    rank) are honoured so the CSV matches what the caller sees. Unlike the
    view there is no 1000-row cap: an export is always complete.
    """
    _require_admin(user_role)
    nr = await _load_nominal_roll_or_404(db, nominal_roll_id)

    conds = [
        Personnel.nominal_roll_id == str(nr.id),
        Personnel.status == "active",
    ]
    if search:
        pattern = f"%{search}%"
        conds.append(
            or_(
                Personnel.full_name.ilike(pattern),
                Personnel.pers_no.ilike(pattern),
            )
        )
    if unit:
        conds.append(Personnel.unit == unit)
    if sub_unit_1:
        conds.append(Personnel.sub_unit_1 == sub_unit_1)
    if sub_unit_2:
        conds.append(Personnel.sub_unit_2 == sub_unit_2)
    if category:
        conds.append(Personnel.category == category)
    if rank:
        conds.append(Personnel.rank == rank)

    personnel_rows = (
        (
            await db.execute(
                select(Personnel)
                .where(*conds)
                .order_by(
                    Personnel.unit,
                    Personnel.sub_unit_1,
                    Personnel.sub_unit_2,
                    Personnel.rank,
                    Personnel.full_name,
                )
            )
        )
        .scalars()
        .all()
    )

    # Tagging overlay (same 1:1 semantics as the browser view): each
    # entry's to_* values override the personnel's canonical unit/sub-units.
    entry_by_personnel: dict[str, TaggingEntry] = {}
    if personnel_rows:
        entries = (
            await db.execute(
                select(TaggingEntry).where(
                    TaggingEntry.personnel_id.in_(
                        [str(p.id) for p in personnel_rows]
                    )
                )
            )
        ).scalars().all()
        entry_by_personnel = {str(e.personnel_id): e for e in entries}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Unit", "Sub Unit 1", "Sub Unit 2", "Sub Unit 3",
            "Category", "Rank", "Full Name", "Pers No", "Callup", "Remarks",
        ]
    )
    for person in personnel_rows:
        entry = entry_by_personnel.get(str(person.id))
        writer.writerow(
            [
                entry.to_unit if entry else person.unit,
                (entry.to_sub_unit_1 if entry else person.sub_unit_1) or "",
                (entry.to_sub_unit_2 if entry else person.sub_unit_2) or "",
                (entry.to_sub_unit_3 if entry else person.sub_unit_3) or "",
                person.category,
                person.rank,
                person.full_name,
                person.pers_no or "",
                person.callup_status,
                person.remarks or "",
            ]
        )

    csv_data = output.getvalue()
    output.close()
    filename = (
        f"nominal_roll_caa_{nr.caa.strftime('%Y%m%d')}_"
        f"{utc_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    return StreamingResponse(
        io.BytesIO(csv_data.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def _load_nominal_roll_or_404(
    db: AsyncSession, nominal_roll_id: str
) -> NominalRoll:
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
    return nr


async def _load_nominal_roll_with_filename(
    db: AsyncSession, nominal_roll_id: str
):
    """Fetch a single nominal roll row joined with its latest CsvUpload's filename."""
    latest_upload = (
        select(
            CsvUpload.nominal_roll_id.label("nominal_roll_id"),
            CsvUpload.original_filename.label("original_filename"),
        )
        .where(CsvUpload.nominal_roll_id == nominal_roll_id)
        .order_by(CsvUpload.uploaded_at.desc())
        .limit(1)
        .subquery()
    )
    return (
        await db.execute(
            select(
                NominalRoll.id,
                NominalRoll.caa,
                NominalRoll.attendance_active,
                NominalRoll.attendance_activated_at,
                NominalRoll.attendance_activated_by,
                NominalRoll.personnel_count,
                NominalRoll.uploaded_at,
                NominalRoll.uploaded_by,
                NominalRoll.csv_hash,
                NominalRoll.label,
                NominalRoll.remarks,
                NominalRoll.notes,
                NominalRoll.created_at,
                latest_upload.c.original_filename,
            )
            .where(NominalRoll.id == nominal_roll_id)
            .outerjoin(
                latest_upload, latest_upload.c.nominal_roll_id == NominalRoll.id
            )
        )
    ).one_or_none()


def _row_to_response(row) -> NominalRollResponse:
    """Build a NominalRollResponse from a joined query row."""
    return NominalRollResponse(
        id=row.id,
        caa=row.caa,
        attendance_active=row.attendance_active,
        personnel_count=row.personnel_count,
        uploaded_at=row.uploaded_at,
        uploaded_by=row.uploaded_by,
        csv_hash=row.csv_hash,
        original_filename=row.original_filename,
        label=row.label,
        remarks=row.remarks,
        notes=row.notes,
        attendance_activated_at=row.attendance_activated_at,
        attendance_activated_by=row.attendance_activated_by,
        created_at=row.created_at,
    )


def _require_admin(user_role: str) -> None:
    if user_role not in ("admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and super admins can view nominal rolls",
        )
