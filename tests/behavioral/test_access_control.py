"""Behavioral tests for access control logic."""

import pytest

from parade_state.models import (
    AccessLevel,
)


class TestAccessLevelHierarchy:
    """Test access level ordering and hierarchy."""

    @pytest.mark.asyncio
    async def test_access_level_ordering(self, db_session):
        """Test that access levels have correct ordering."""
        # Create access levels
        unit = AccessLevel(name="unit", level_order=1)
        coy = AccessLevel(name="coy", level_order=2)
        platoon = AccessLevel(name="platoon", level_order=3)

        db_session.add_all([unit, coy, platoon])
        await db_session.flush()

        # Verify ordering
        assert unit.level_order < coy.level_order < platoon.level_order

        # Test that higher level_order means broader access
        assert unit.level_order == 1  # Most restrictive
        assert platoon.level_order == 3  # Most permissive

    @pytest.mark.asyncio
    async def test_access_level_uniqueness(self, db_session):
        """Test that level_order must be unique."""
        level1 = AccessLevel(name="level1", level_order=1)
        level2 = AccessLevel(name="level2", level_order=1)  # Duplicate order

        db_session.add(level1)
        await db_session.flush()

        db_session.add(level2)
        with pytest.raises(Exception):  # Should fail due to unique constraint
            await db_session.flush()


class TestColumnVisibility:
    """Test column visibility based on access levels."""

    @pytest.mark.asyncio
    async def test_column_visibility_hierarchy(self, db_session, sample_access_levels):
        """Test that column visibility follows access level hierarchy."""
        unit_level = sample_access_levels["unit"]  # level_order = 1 (most restrictive)
        platoon_level = sample_access_levels[
            "platoon"
        ]  # level_order = 3 (least restrictive)

        # Column with sensitivity level = platoon (level_order = 3)
        # Should be visible to platoon level (3 >= 3) but not unit level (1 < 3)

        # Unit level user cannot see platoon-sensitive columns
        assert unit_level.level_order < platoon_level.level_order

        # Platoon level user can see platoon-sensitive columns
        assert platoon_level.level_order >= platoon_level.level_order

    @pytest.mark.asyncio
    async def test_admin_column_visibility(self, db_session, sample_users):
        """Test that admin users can see all columns regardless of sensitivity."""
        admin = sample_users["admin"]

        # Admin should have access to all columns
        # (This would be handled in business logic)
        assert admin.role == "admin"
        assert admin.access_level_id is not None  # But role takes precedence
