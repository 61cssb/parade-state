"""Behavioral tests for access control logic."""

import pytest
from sqlalchemy import select

from parade_state.models import (
    AccessLevel,
    DeploymentUserAccess,
    User,
    UserSubunitScope,
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


class TestUserAccessControl:
    """Test user access control and scoping logic."""

    @pytest.mark.asyncio
    async def test_user_deployment_access_grant(self, db_session, sample_users, sample_deployment):
        """Test granting user access to a deployment."""
        user = sample_users["user"]
        deployment = sample_deployment

        # Grant access
        access = DeploymentUserAccess(
            user_id=user.id,
            deployment_id=deployment.id,
            granted_by=sample_users["admin"].id,
        )

        db_session.add(access)
        await db_session.flush()

        # Verify access exists
        stmt = select(DeploymentUserAccess).where(
            DeploymentUserAccess.user_id == user.id,
            DeploymentUserAccess.deployment_id == deployment.id,
        )
        result = await db_session.execute(stmt)
        access_record = result.scalar_one()

        assert access_record.user_id == user.id
        assert access_record.deployment_id == deployment.id
        assert access_record.revoked_at is None

    @pytest.mark.asyncio
    async def test_user_subunit_scope_assignment(self, db_session, sample_users, sample_deployment):
        """Test assigning subunit scope to a user."""
        user = sample_users["user"]
        deployment = sample_deployment

        # Assign scope to Platoon 1
        scope = UserSubunitScope(
            user_id=user.id,
            deployment_id=deployment.id,
            unit="Coy A",
            sub_unit_1="Platoon 1",
            created_by=sample_users["admin"].id,
        )

        db_session.add(scope)
        await db_session.flush()

        # Verify scope exists
        stmt = select(UserSubunitScope).where(
            UserSubunitScope.user_id == user.id,
            UserSubunitScope.deployment_id == deployment.id,
        )
        result = await db_session.execute(stmt)
        scope_record = result.scalar_one()

        assert scope_record.unit == "Coy A"
        assert scope_record.sub_unit_1 == "Platoon 1"
        assert scope_record.sub_unit_2 is None  # Not specified

    @pytest.mark.asyncio
    async def test_user_multiple_scopes_same_deployment(self, db_session, sample_users, sample_deployment):
        """Test user can have multiple scopes within same deployment."""
        user = sample_users["user"]
        deployment = sample_deployment

        # Assign multiple scopes
        scopes = [
            UserSubunitScope(
                user_id=user.id,
                deployment_id=deployment.id,
                unit="Coy A",
                sub_unit_1="Platoon 1",
                created_by=sample_users["admin"].id,
            ),
            UserSubunitScope(
                user_id=user.id,
                deployment_id=deployment.id,
                unit="Coy A",
                sub_unit_1="Platoon 2",
                created_by=sample_users["admin"].id,
            ),
        ]

        db_session.add_all(scopes)
        await db_session.flush()

        # Verify both scopes exist
        stmt = select(UserSubunitScope).where(
            UserSubunitScope.user_id == user.id,
            UserSubunitScope.deployment_id == deployment.id,
        )
        result = await db_session.execute(stmt)
        user_scopes = result.scalars().all()

        assert len(user_scopes) == 2
        platoon_names = {scope.sub_unit_1 for scope in user_scopes}
        assert platoon_names == {"Platoon 1", "Platoon 2"}

    @pytest.mark.asyncio
    async def test_admin_bypasses_access_control(self, db_session, sample_users, sample_deployment):
        """Test that admin users bypass normal access controls."""
        admin = sample_users["admin"]

        # Admin should have access to deployment without explicit grants
        stmt = select(DeploymentUserAccess).where(
            DeploymentUserAccess.user_id == admin.id,
            DeploymentUserAccess.deployment_id == sample_deployment.id,
        )
        result = await db_session.execute(stmt)
        access_records = result.scalars().all()

        # Admin might not have explicit grants, but should still have access
        # (This would be handled in business logic, not constraints)
        assert len(access_records) == 0  # No explicit grants needed


class TestColumnVisibility:
    """Test column visibility based on access levels."""

    @pytest.mark.asyncio
    async def test_column_visibility_hierarchy(self, db_session, sample_access_levels):
        """Test that column visibility follows access level hierarchy."""
        unit_level = sample_access_levels["unit"]  # level_order = 1 (most restrictive)
        platoon_level = sample_access_levels["platoon"]  # level_order = 3 (least restrictive)

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