"""Integration-test fixtures.

Postgres enforces foreign keys that SQLite silently ignores, so the
well-known identity strings integration tests use for created_by /
granted_by / user_id parameters must exist as real ``users`` rows.
This fixture seeds them once per test alongside the other sample data.

Since issue 31 the API derives caller identity from the session (cookie
or Bearer), never from query parameters — tests act as a user via the
``client_as`` factory, which mints a real ``UserSession`` row through
the same engine the app under test resolves sessions with.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.session import create_user_session
from parade_state.models import User
from parade_state.utils.cookies import AUTH_COOKIE_NAME

# (id, email, role) for every well-known identity referenced by
# integration tests. Keep in sync with hardcoded usages in test files.
WELL_KNOWN_USERS = (
    ("admin-user-id", "admin-well-known@example.com", "admin"),
    ("super-admin-test-id", "super-admin-test@example.com", "super_admin"),
    ("super-admin-user-id", "super-admin-user@example.com", "super_admin"),
    ("super-admin-id", "super-admin@example.com", "super_admin"),
    ("user-id", "well-known-user@example.com", "user"),
)

# Role shorthand accepted by client_as() → canonical well-known user id.
ROLE_SHORTHAND_IDS = {
    "admin": "admin-user-id",
    "super_admin": "super-admin-test-id",
    "user": "user-id",
}


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


@pytest.fixture
async def client_as(
    client: TestClient,
    db_session: AsyncSession,
    well_known_users: dict[str, User],
):
    """Factory: authenticate the test client as a user via a session cookie.

    Mints a ``UserSession`` row (the same mechanism the OAuth callback
    uses) and sets the HttpOnly ``session_token`` cookie on the client, so
    same-origin requests authenticate exactly as they do in the admin UI.

    Accepts a well-known user id (e.g. ``"super-admin-user-id"``), a role
    shorthand (``"admin"``, ``"super_admin"``, ``"user"``), or a ``User``
    object (e.g. from ``sample_users``). Returns the client with the
    cookie set.
    """

    async def _client_as(user: str | User) -> TestClient:
        if isinstance(user, User):
            target = user
        else:
            target = well_known_users[ROLE_SHORTHAND_IDS.get(user, user)]
        session = await create_user_session(
            db_session,
            user_id=str(target.id),
            email=target.email,
            name=target.name,
            role=target.role,
        )
        client.cookies.set(AUTH_COOKIE_NAME, session.token)
        return client

    return _client_as
