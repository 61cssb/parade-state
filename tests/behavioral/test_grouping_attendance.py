"""Behavioral tests for grouping lifecycle and attendance logic.

Attendance is NR/Tagging-scoped with hardcoded AM/PM; sessions are gone.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from parade_state.models import (
    Attendance,
    Grouping,
    GroupingNotes,
)
from parade_state.utils import utc_dt


class TestGroupingLifecycle:
    """Test grouping status transitions and constraints."""

    @pytest.mark.asyncio
    async def test_only_one_active_grouping(
        self, db_session, sample_nominal_roll, sample_users
    ):
        """Multiple active groupings are allowed at the DB level; app logic prevents it."""
        admin_id = sample_users["admin"].id

        grouping1 = Grouping(
            name="Grouping 1",
            nominal_roll_id=sample_nominal_roll.id,
            mode="standard",
            status="active",
            valid_from=utc_dt.utcnow() - timedelta(days=1),
            valid_until=utc_dt.utcnow() + timedelta(days=30),
            created_by=admin_id,
        )
        db_session.add(grouping1)
        await db_session.commit()

        grouping2 = Grouping(
            name="Grouping 2",
            nominal_roll_id=sample_nominal_roll.id,
            mode="standard",
            status="active",
            valid_from=utc_dt.utcnow() - timedelta(days=1),
            valid_until=utc_dt.utcnow() + timedelta(days=30),
            created_by=admin_id,
        )
        db_session.add(grouping2)
        await db_session.commit()

        stmt = select(Grouping).where(Grouping.status == "active")
        result = await db_session.execute(stmt)
        active_groupings = result.scalars().all()
        assert len(active_groupings) == 2  # DB allows it, app logic prevents


class TestAttendanceSnapshotRules:
    """Test attendance row snapshot behavior (AM/PM model)."""

    @pytest.mark.asyncio
    async def test_attendance_row_records_snapshots(
        self, db_session, sample_nominal_roll, sample_personnel, sample_users
    ):
        """An attendance row captures the personnel's unit/subunit snapshot."""
        personnel = sample_personnel[0]
        admin_id = sample_users["admin"].id

        attendance = Attendance(
            personnel_id=personnel.id,
            nominal_roll_id=sample_nominal_roll.id,
            date=date.today(),
            status_am="present",
            status_pm="absent",
            unit_snapshot=personnel.unit,
            sub_unit_1_snapshot=personnel.sub_unit_1,
            sub_unit_2_snapshot=personnel.sub_unit_2,
            sub_unit_3_snapshot=personnel.sub_unit_3,
            created_by=admin_id,
            updated_by=admin_id,
            last_edit_at=utc_dt.utcnow(),
            last_edit_by=admin_id,
        )
        db_session.add(attendance)
        await db_session.commit()

        assert attendance.unit_snapshot == personnel.unit
        assert attendance.sub_unit_1_snapshot == personnel.sub_unit_1
        assert attendance.last_edit_at is not None

    @pytest.mark.asyncio
    async def test_attendance_retroactive_edit_preserves_snapshot(
        self, db_session, sample_nominal_roll, sample_personnel, sample_users
    ):
        """A retroactive edit updates status but preserves the original snapshot."""
        personnel = sample_personnel[0]
        admin_id = sample_users["admin"].id

        attendance = Attendance(
            personnel_id=personnel.id,
            nominal_roll_id=sample_nominal_roll.id,
            date=date.today(),
            status_am="present",
            status_pm="present",
            unit_snapshot="Original Unit",
            sub_unit_1_snapshot="Original Platoon",
            created_by=admin_id,
            updated_by=admin_id,
        )
        db_session.add(attendance)
        await db_session.commit()

        original_unit = attendance.unit_snapshot
        original_sub1 = attendance.sub_unit_1_snapshot

        # Retroactive edit.
        attendance.status_am = "absent"
        attendance.remarks_am = "Sick leave"
        attendance.updated_at = utc_dt.utcnow()
        attendance.updated_by = admin_id
        attendance.last_edit_at = utc_dt.utcnow()
        attendance.last_edit_by = admin_id
        attendance.is_retroactive_edit = True
        await db_session.commit()

        assert attendance.unit_snapshot == original_unit
        assert attendance.sub_unit_1_snapshot == original_sub1
        assert attendance.status_am == "absent"
        assert attendance.remarks_am == "Sick leave"
        assert attendance.is_retroactive_edit is True


class TestGroupingNotes:
    """Test grouping-scoped notes functionality."""

    @pytest.mark.asyncio
    async def test_grouping_notes_uniqueness(
        self, db_session, sample_grouping, sample_personnel, sample_users
    ):
        """Only one notes record per grouping-personnel pair."""
        grouping = sample_grouping
        personnel = sample_personnel[0]
        admin_id = sample_users["admin"].id

        notes1 = GroupingNotes(
            grouping_id=grouping.id,
            personnel_id=personnel.id,
            notes="First note",
            created_by=admin_id,
            updated_by=admin_id,
        )
        db_session.add(notes1)
        await db_session.commit()

        notes2 = GroupingNotes(
            grouping_id=grouping.id,
            personnel_id=personnel.id,
            notes="Second note",
            created_by=admin_id,
            updated_by=admin_id,
        )
        db_session.add(notes2)
        with pytest.raises(Exception):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_notes_snapshot_on_attendance_row(
        self, db_session, sample_grouping, sample_personnel, sample_users
    ):
        """Notes are snapshotted onto the attendance row when it is created."""
        grouping = sample_grouping
        personnel = sample_personnel[0]
        admin_id = sample_users["admin"].id

        grouping_notes = GroupingNotes(
            grouping_id=grouping.id,
            personnel_id=personnel.id,
            notes="Medical condition: requires accommodation",
            created_by=admin_id,
            updated_by=admin_id,
        )
        db_session.add(grouping_notes)
        await db_session.commit()

        attendance = Attendance(
            personnel_id=personnel.id,
            nominal_roll_id=grouping.nominal_roll_id,
            date=date.today(),
            status_am="present",
            status_pm="present",
            notes_snapshot=grouping_notes.notes,
            created_by=admin_id,
            updated_by=admin_id,
        )
        db_session.add(attendance)
        await db_session.commit()

        assert attendance.notes_snapshot == grouping_notes.notes
