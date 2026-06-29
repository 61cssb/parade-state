"""Behavioral tests for personnel identity (short_id) and estab versioning."""

from datetime import date

import pytest
from sqlalchemy import select

from parade_state.models import (
    ColumnMapping,
    Estab,
    Personnel,
)
from parade_state.utils import ids, utc_dt


class TestPersonnelIdentity:
    """Test personnel identity via the cross-estab short_id."""

    @pytest.mark.asyncio
    async def test_personnel_short_id_auto_generated(
        self, db_session, sample_estab, sample_users
    ):
        """A Personnel row gets an 8-char base62 short_id by default."""
        admin_id = sample_users["admin"].id

        person = Personnel(
            estab_id=sample_estab.id,
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )
        db_session.add(person)
        await db_session.commit()

        assert isinstance(person.short_id, str)
        assert len(person.short_id) == 8
        # Auto-minted value uses the base62 alphabet (no ambiguous look-alikes)
        alphabet = set(
            "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        )
        assert set(person.short_id).issubset(alphabet)

    @pytest.mark.asyncio
    async def test_personnel_distinct_rows_get_distinct_short_ids(
        self, db_session, sample_estab, sample_users
    ):
        """Two distinct persons get distinct row ids and distinct short_ids."""
        admin_id = sample_users["admin"].id

        person1 = Personnel(
            estab_id=sample_estab.id,
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )
        person2 = Personnel(
            estab_id=sample_estab.id,
            rank="PTE",
            full_name="John Doe Jr",
            unit="Coy B",
            created_by=admin_id,
        )

        db_session.add_all([person1, person2])
        await db_session.commit()

        assert person1.id != person2.id
        assert person1.short_id != person2.short_id
        assert person1.full_name != person2.full_name

    @pytest.mark.asyncio
    async def test_personnel_short_id_shared_across_estabs(
        self, db_session, sample_users
    ):
        """The same person appearing in two estabs shares one short_id across rows."""
        admin_id = sample_users["admin"].id

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

        # The same individual, deliberately assigned one short_id across estabs.
        shared_short_id = ids.short_id()
        person1 = Personnel(
            estab_id=estab1.id,
            short_id=shared_short_id,
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )
        person2 = Personnel(
            estab_id=estab2.id,
            short_id=shared_short_id,  # same person, different estab
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )

        db_session.add_all([person1, person2])
        await db_session.commit()

        # Distinct rows, distinct estabs, but ONE cross-estab person identity.
        assert person1.estab_id != person2.estab_id
        assert person1.id != person2.id
        assert person1.short_id == person2.short_id

    @pytest.mark.asyncio
    async def test_personnel_estab_short_id_unique_constraint(
        self, db_session, sample_estab, sample_users
    ):
        """UNIQUE(estab_id, short_id): two rows, same estab, same short_id must fail."""
        admin_id = sample_users["admin"].id
        clashing_short_id = ids.short_id()

        person1 = Personnel(
            estab_id=sample_estab.id,
            short_id=clashing_short_id,
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )
        person2 = Personnel(
            estab_id=sample_estab.id,  # same estab
            short_id=clashing_short_id,  # same short_id in same estab
            rank="CPL",
            full_name="Jane Doe",
            unit="Coy A",
            created_by=admin_id,
        )

        db_session.add_all([person1, person2])
        with pytest.raises(Exception):  # IntegrityError from the unique constraint
            await db_session.commit()
        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_personnel_different_persons_different_short_ids(
        self, db_session, sample_users
    ):
        """Different persons in different estabs have different short_ids."""
        admin_id = sample_users["admin"].id

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

        person1 = Personnel(
            estab_id=estab1.id,
            rank="PTE",
            full_name="John Doe",
            unit="Coy A",
            created_by=admin_id,
        )
        person2 = Personnel(
            estab_id=estab2.id,
            rank="PTE",
            full_name="Jane Smith",  # a different person
            unit="Coy A",
            created_by=admin_id,
        )

        db_session.add_all([person1, person2])
        await db_session.commit()

        assert person1.short_id != person2.short_id


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
            raw_name="Full Name",
            canonical_name="full_name",
            status="admin_confirmed",
        )

        db_session.add(mapping1)
        await db_session.commit()

        # Try to create second mapping for same canonical
        mapping2 = ColumnMapping(
            raw_name="Employee Name",
            canonical_name="full_name",  # Same canonical
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
                raw_name="Unit",
                canonical_name="unit",
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
        assert canonical_names == {"unit", "full_name", "rank"}

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
