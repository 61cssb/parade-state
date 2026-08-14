"""Behavioral tests for deployment lifecycle and attendance logic.

Attendance is NR/Tagging-scoped with hardcoded AM/PM; sessions are gone.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from parade_state.models import (
    Attendance,
    Deployment,
    DeploymentNotes,
)
from parade_state.utils import utc_dt


class TestDeploymentLifecycle:
    """Test deployment status transitions and constraints."""

    @pytest.mark.asyncio
    async def test_only_one_active_deployment(
        self, db_session, sample_nominal_roll, sample_users
    ):
        """Multiple active deployments are allowed at the DB level; app logic prevents it."""
        admin_id = sample_users["admin"].id

        deployment1 = Deployment(
            name="Deployment 1",
            nominal_roll_id=sample_nominal_roll.id,
            status="active",
            valid_from=utc_dt.utcnow() - timedelta(days=1),
            valid_until=utc_dt.utcnow() + timedelta(days=30),
            created_by=admin_id,
        )
        db_session.add(deployment1)
        await db_session.commit()

        deployment2 = Deployment(
            name="Deployment 2",
            nominal_roll_id=sample_nominal_roll.id,
            status="active",
            valid_from=utc_dt.utcnow() - timedelta(days=1),
            valid_until=utc_dt.utcnow() + timedelta(days=30),
            created_by=admin_id,
        )
        db_session.add(deployment2)
        await db_session.commit()

        stmt = select(Deployment).where(Deployment.status == "active")
        result = await db_session.execute(stmt)
        active_deployments = result.scalars().all()
        assert len(active_deployments) == 2  # DB allows it, app logic prevents


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
            tagging_id=None,
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
            tagging_id=None,
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


class TestDeploymentNotes:
    """Test deployment-scoped notes functionality."""

    @pytest.mark.asyncio
    async def test_deployment_notes_uniqueness(
        self, db_session, sample_deployment, sample_personnel, sample_users
    ):
        """Only one notes record per deployment-personnel pair."""
        deployment = sample_deployment
        personnel = sample_personnel[0]
        admin_id = sample_users["admin"].id

        notes1 = DeploymentNotes(
            deployment_id=deployment.id,
            personnel_id=personnel.id,
            notes="First note",
            created_by=admin_id,
            updated_by=admin_id,
        )
        db_session.add(notes1)
        await db_session.commit()

        notes2 = DeploymentNotes(
            deployment_id=deployment.id,
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
        self, db_session, sample_deployment, sample_personnel, sample_users
    ):
        """Notes are snapshotted onto the attendance row when it is created."""
        deployment = sample_deployment
        personnel = sample_personnel[0]
        admin_id = sample_users["admin"].id

        deployment_notes = DeploymentNotes(
            deployment_id=deployment.id,
            personnel_id=personnel.id,
            notes="Medical condition: requires accommodation",
            created_by=admin_id,
            updated_by=admin_id,
        )
        db_session.add(deployment_notes)
        await db_session.commit()

        attendance = Attendance(
            personnel_id=personnel.id,
            nominal_roll_id=deployment.nominal_roll_id,
            tagging_id=None,
            date=date.today(),
            status_am="present",
            status_pm="present",
            notes_snapshot=deployment_notes.notes,
            created_by=admin_id,
            updated_by=admin_id,
        )
        db_session.add(attendance)
        await db_session.commit()

        assert attendance.notes_snapshot == deployment_notes.notes
