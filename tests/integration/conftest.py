"""Integration-test fixtures.

Postgres enforces foreign keys that SQLite silently ignores, so the
well-known identity strings integration tests use for created_by /
granted_by / user_id parameters must exist as real ``users`` rows.
This fixture seeds them once per test alongside the other sample data.
"""

import pytest

from parade_state.models import User

# (id, email, role) for every well-known identity referenced by
# integration tests. Keep in sync with hardcoded usages in test files.
WELL_KNOWN_USERS = (
    ("admin-user-id", "admin-well-known@example.com", "admin"),
    ("super-admin-test-id", "super-admin-test@example.com", "super_admin"),
    ("super-admin-user-id", "super-admin-user@example.com", "super_admin"),
    ("super-admin-id", "super-admin@example.com", "super_admin"),
    ("user-id", "well-known-user@example.com", "user"),
)


@pytest.fixture(autouse=True)
async def well_known_users(db_session, sample_access_levels):
    """Create the well-known user identities integration tests reference."""
    access_level_id = str(sample_access_levels["unit"].id)

    users = [
        User(
            id=user_id,
            email=email,
            name=email.removesuffix("@example.com"),
            role=role,
            status="active",
            access_level_id=access_level_id,
        )
        for user_id, email, role in WELL_KNOWN_USERS
    ]

    db_session.add_all(users)
    await db_session.commit()

    return {user.id: user for user in users}
