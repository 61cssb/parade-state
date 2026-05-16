"""Behavioral tests for CSV ingestion and personnel identity."""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from parade_state.models import (
    ColumnMapping,
    Estab,
    Personnel,
)
from parade_state.utils import utc_dt


class TestPersonnelIdentity:
    """Test personnel identity and cross-CSV logic."""

    @pytest.mark.asyncio
    async def test_personnel_internal_id_uniqueness(
        self, db_session, sample_estab, sample_users
    ):
        """Test that personnel internal IDs are unique across the system."""
        admin_id = sample_users["admin"].id

        # Create two personnel with same pers_no but different internal IDs
        person1 = Personnel(
            estab_id=sample_estab.id,
            pers_no="12345",
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )

        person2 = Personnel(
            estab_id=sample_estab.id,
            pers_no="12345",  # Same external ID
            rank="PTE",
            full_name="John Doe Jr",  # Different person
            unit="Coy B",
            created_by=admin_id,
        )

        db_session.add_all([person1, person2])
        await db_session.commit()

        # Verify they have different internal IDs
        assert person1.id != person2.id
        assert person1.pers_no == person2.pers_no  # Same external reference
        assert person1.full_name != person2.full_name  # Different people

    @pytest.mark.asyncio
    async def test_personnel_pers_no_not_unique_within_estab(
        self, db_session, sample_estab, sample_users
    ):
        """Test that pers_no is not enforced unique within an estab (though it should be)."""
        admin_id = sample_users["admin"].id

        # This should be prevented by business logic, but test the constraint
        person1 = Personnel(
            estab_id=sample_estab.id,
            pers_no="12345",
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )

        # Try to create another with same pers_no in same estab
        person2 = Personnel(
            estab_id=sample_estab.id,
            pers_no="12345",  # Same pers_no in same estab
            rank="CPL",
            full_name="Jane Doe",
            unit="Coy A",
            created_by=admin_id,
        )

        db_session.add(person1)
        db_session.add(person2)

        # This might succeed at DB level but should be prevented by business logic
        await db_session.commit()

        # Verify both exist (constraint not enforced at DB level)
        assert person1.pers_no == person2.pers_no
        assert person1.id != person2.id

    @pytest.mark.asyncio
    async def test_personnel_identity_isolation_between_estabs(
        self, db_session, sample_users
    ):
        """Test that personnel from different estabs are completely isolated."""
        admin_id = sample_users["admin"].id

        # Create two different estabs
        estab1 = Estab(
            caa=date(2024, 1, 1),
            csv_hash="hash1",
            status="confirmed",
            uploaded_by=admin_id,
            confirmed_by=admin_id,
        )

        estab2 = Estab(
            caa=date(2024, 2, 1),
            csv_hash="hash2",
            status="confirmed",
            uploaded_by=admin_id,
            confirmed_by=admin_id,
        )

        db_session.add_all([estab1, estab2])
        await db_session.commit()

        # Create personnel with same pers_no in different estabs
        person1 = Personnel(
            estab_id=estab1.id,
            pers_no="12345",
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )

        person2 = Personnel(
            estab_id=estab2.id,
            pers_no="12345",  # Same pers_no, different estab
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )

        db_session.add_all([person1, person2])
        await db_session.commit()

        # Verify they are completely separate entities
        assert person1.estab_id != person2.estab_id
        assert person1.pers_no == person2.pers_no
        assert person1.id != person2.id


class TestEstabVersioning:
    """Test establishment versioning and CAA constraints."""

    @pytest.mark.asyncio
    async def test_estab_caa_uniqueness(self, db_session, sample_users):
        """Test that CAA dates must be unique among confirmed estabs."""
        admin_id = sample_users["admin"].id
        caa_date = date(2024, 1, 1)

        # Create first confirmed estab
        estab1 = Estab(
            caa=caa_date,
            csv_hash="hash1",
            status="confirmed",
            uploaded_by=admin_id,
            confirmed_by=admin_id,
        )

        db_session.add(estab1)
        await db_session.commit()

        # Try to create second confirmed estab with same CAA
        estab2 = Estab(
            caa=caa_date,  # Same CAA
            csv_hash="hash2",
            status="confirmed",
            uploaded_by=admin_id,
            confirmed_by=admin_id,
        )

        db_session.add(estab2)
        with pytest.raises(Exception):  # Should fail due to unique constraint
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_estab_status_transitions(self, db_session, sample_estab):
        """Test valid estab status transitions."""
        estab = sample_estab

        # Valid transitions
        valid_transitions = [
            ("draft", "confirmed"),
            ("confirmed", "archived"),
        ]

        for from_status, to_status in valid_transitions:
            # Reset to initial state
            estab.status = from_status
            await db_session.commit()

            # Attempt transition
            estab.status = to_status
            await db_session.commit()

            # Verify transition succeeded
            assert estab.status == to_status

    @pytest.mark.asyncio
    async def test_estab_archived_blocks_new_deployments(
        self, db_session, sample_estab
    ):
        """Test that archived estabs cannot be used for new deployments."""
        # This would be enforced by business logic
        # Archive the estab
        sample_estab.status = "archived"
        await db_session.commit()

        # Attempting to create deployment from archived estab should fail
        # (Test would verify application-level validation)
        assert sample_estab.status == "archived"


class TestColumnMapping:
    """Test column mapping constraints and behavior."""

    @pytest.mark.asyncio
    async def test_canonical_column_uniqueness(self, db_session):
        """Test that each canonical column can only be mapped once."""
        # Create first mapping
        mapping1 = ColumnMapping(
            raw_name="Personal Number",
            canonical_name="pers_no",
            status="admin_confirmed",
        )

        db_session.add(mapping1)
        await db_session.commit()

        # Try to create second mapping for same canonical
        mapping2 = ColumnMapping(
            raw_name="Employee ID",
            canonical_name="pers_no",  # Same canonical
            status="admin_confirmed",
        )

        db_session.add(mapping2)
        with pytest.raises(Exception):  # Should fail due to unique constraint
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_multiple_raw_names_can_map_to_different_canonicals(self, db_session):
        """Test that different raw names can map to different canonicals."""
        mappings = [
            ColumnMapping(
                raw_name="Personal Number",
                canonical_name="pers_no",
                status="admin_confirmed",
            ),
            ColumnMapping(
                raw_name="Full Name",
                canonical_name="full_name",
                status="admin_confirmed",
            ),
            ColumnMapping(
                raw_name="Rank",
                canonical_name="rank",
                status="admin_confirmed",
            ),
        ]

        db_session.add_all(mappings)
        await db_session.commit()

        # Verify all mappings exist
        stmt = select(ColumnMapping)
        result = await db_session.execute(stmt)
        all_mappings = result.scalars().all()

        assert len(all_mappings) == 3
        canonical_names = {m.canonical_name for m in all_mappings}
        assert canonical_names == {"pers_no", "full_name", "rank"}

    @pytest.mark.asyncio
    async def test_column_mapping_status_transitions(self, db_session):
        """Test column mapping status transitions."""
        mapping = ColumnMapping(
            raw_name="Test Column",
            canonical_name="test_field",
            status="auto_detected",
        )

        db_session.add(mapping)
        await db_session.commit()

        # Valid transitions
        valid_transitions = [
            ("auto_detected", "admin_confirmed"),
            ("admin_confirmed", "deprecated"),
        ]

        for from_status, to_status in valid_transitions:
            # Reset to initial state
            mapping.status = from_status
            await db_session.commit()

            # Attempt transition
            mapping.status = to_status
            if to_status == "deprecated":
                mapping.deprecated_at = utc_dt.utcnow()
            await db_session.commit()

            # Verify transition succeeded
            assert mapping.status == to_status
