"""Unit Strength report (issues 25 and 36).

The report aggregates the attendance-active NR's parade state into the
strength reporting format: Officer/WOSE/Total column groups of
In/Out/Current/%, grouped by effective sub_unit_1 (shown once) and
sub_unit_2 with SUBTOTALs and a unit TOTAL. In counts non-deferred
personnel, Current the present marks (single daily session, issue 33 —
reason never participates), Out everyone else (unmarked = absent).

Reporting basis (issue 36): tagged (default, every role) groups under
the tagging-applied effective subunits; untagged (super-admin only)
groups under the original NR allocations from the canonical Personnel
columns.
"""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.session import create_user_session
from parade_state.config import get_settings
from parade_state.main import app as main_app
from parade_state.models import (
    Attendance,
    Personnel,
    Tagging,
    TaggingEntry,
    User,
    UserSubunitAssignment,
)
from parade_state.utils.cookies import AUTH_COOKIE_NAME

TODAY = date.today()


async def _sign_in(
    client: TestClient, db_session: AsyncSession, user: User
) -> None:
    """Create a session for ``user`` and set the auth cookie on ``client``."""
    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )
    await db_session.commit()
    client.cookies.set(AUTH_COOKIE_NAME, session.token)


async def _make_super_admin(db_session: AsyncSession) -> User:
    user = User(
        email="sa@example.com", name="Super Admin", status="active", role="super_admin"
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _raw(response) -> str:
    """Response body with collapsed whitespace (markup kept)."""
    return re.sub(r"\s+", " ", response.text)


def _text(response) -> str:
    """Tag-stripped body with collapsed whitespace — a row renders as e.g.
    ``Section 1 0 0 0 0% 1 0 1 100% 1 0 1 100%`` (name then Officer/WOSE/
    Total cells of In/Out/Current/%)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.text))


def _get(client: TestClient, basis: str | None = None):
    params = {"date": TODAY.isoformat()}
    if basis is not None:
        params["basis"] = basis
    return client.get("/admin", params=params)


# --- Auth / shell ---


@pytest.mark.asyncio
async def test_signed_out_redirects_to_login(client: TestClient):
    """The report is admin-only: anonymous visitors are sent to login."""
    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("/auth/login")


@pytest.mark.asyncio
async def test_no_active_roll_shows_empty_state(
    client: TestClient, db_session: AsyncSession, sample_users
):
    """Without an attendance-active NR there is nothing to report."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = _get(client)

    assert response.status_code == 200
    assert "No nominal roll is active for attendance" in response.text


# --- The report itself ---


@pytest.mark.asyncio
async def test_super_admin_sees_full_report(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """Full-unit report: grouped rows, SUBTOTALs, TOTAL, and the date
    control (no slot toggle — single session, issue 33). Sample roster:
    2 WOSE in Platoon 1 (John absent-with-mc, Jane present), 1 Officer in
    Platoon 2 (unmarked = absent)."""
    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    response = _get(client)
    assert response.status_code == 200
    raw = _raw(response)
    assert 'name="date"' in raw
    assert 'name="slot"' not in raw  # AM/PM toggle is gone
    body = _text(response)

    # Sections in order, sub_unit_1 shown once per section.
    assert body.index("Platoon 1") < body.index("Platoon 2")

    # Row: Platoon 1 / Section 1 — WOSE absent (mc) → out, no Officer.
    assert "Section 1 0 0 0 0% 1 1 0 0% 1 1 0 0%" in body
    # Row: Platoon 1 / Section 2 — WOSE present → current.
    assert "Section 2 0 0 0 0% 1 0 1 100% 1 0 1 100%" in body
    # Platoon 1 SUBTOTAL: WOSE 2 in, 1 current.
    assert "SUBTOTAL 0 0 0 0% 2 1 1 50% 2 1 1 50%" in body
    # Row: Platoon 2 / Section 1 — Officer unmarked → out.
    assert "Section 1 1 1 0 0% 0 0 0 0% 1 1 0 0%" in body
    assert "SUBTOTAL 1 1 0 0% 0 0 0 0% 1 1 0 0%" in body
    # Unit TOTAL: Officer 1 out, WOSE 1 of 2, 1 of 3 = 33%.
    assert "TOTAL 1 1 0 0% 2 1 1 50% 3 2 1 33%" in body


@pytest.mark.asyncio
async def test_reason_never_affects_buckets(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """Reason classifies remarks but never feeds reporting: absent rows —
    with or without a reason — are Out; only present is Current."""
    admin_id = str(sample_users["admin"].id)
    db_session.add(
        Attendance(
            personnel_id=str(sample_personnel[2].id),
            nominal_roll_id=str(sample_personnel[2].nominal_roll_id),
            date=TODAY,
            status="absent",
            reason="awol",
            created_by=admin_id,
            updated_by=admin_id,
        )
    )
    await db_session.commit()

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    body = _text(_get(client))

    # Officer absent (awol) → Out; John absent (mc, from the fixture) → Out;
    # only Jane's present counts Current.
    assert "Section 1 1 1 0 0% 0 0 0 0% 1 1 0 0%" in body  # Officer
    assert "Section 1 0 0 0 0% 1 1 0 0% 1 1 0 0%" in body  # John
    assert "TOTAL 1 1 0 0% 2 1 1 50% 3 2 1 33%" in body


@pytest.mark.asyncio
async def test_deferred_and_archived_excluded_from_in(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """In counts only active non-deferred personnel: a deferred and an
    archived person (same subunits, some marked present) must not move
    any number."""
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_personnel[0].nominal_roll_id)
    db_session.add_all(
        [
            Personnel(
                nominal_roll_id=nr_id,
                pers_no="10000009",
                rank="PTE",
                category="WOSE",
                full_name="Deferred Person",
                unit="Coy A",
                sub_unit_1="Platoon 1",
                sub_unit_2="Section 1",
                inpro_status="deferred",
                created_by=admin_id,
            ),
            Personnel(
                nominal_roll_id=nr_id,
                pers_no="10000010",
                rank="PTE",
                category="WOSE",
                full_name="Archived Person",
                unit="Coy A",
                sub_unit_1="Platoon 1",
                sub_unit_2="Section 1",
                status="archived",
                created_by=admin_id,
            ),
        ]
    )
    await db_session.commit()

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    body = _text(_get(client))

    assert "Section 1 0 0 0 0% 1 1 0 0% 1 1 0 0%" in body
    assert "TOTAL 1 1 0 0% 2 1 1 50% 3 2 1 33%" in body


@pytest.mark.asyncio
async def test_tagging_overlay_regroups_rows(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """Effective (tagged) subunits drive the grouping: remapping the
    Officer into Platoon 1 / Section 3 moves his row there."""
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_personnel[0].nominal_roll_id)
    tagging = Tagging(label="Exercise", nominal_roll_id=nr_id, created_by=admin_id)
    db_session.add(tagging)
    await db_session.flush()
    db_session.add(
        TaggingEntry(
            tagging_id=str(tagging.id),
            personnel_id=str(sample_personnel[2].id),
            from_unit="Coy A",
            from_sub_unit_1="Platoon 2",
            from_sub_unit_2="Section 1",
            to_unit="Coy A",
            to_sub_unit_1="Platoon 1",
            to_sub_unit_2="Section 3",
        )
    )
    await db_session.commit()

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    body = _text(_get(client))

    assert "Section 3 1 1 0 0% 0 0 0 0% 1 1 0 0%" in body
    assert "Platoon 2" not in body
    assert "SUBTOTAL 1 1 0 0% 2 1 1 50% 3 2 1 33%" in body  # Platoon 1


@pytest.mark.asyncio
async def test_null_subunits_reported_in_none_bucket(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """Personnel without subunits still count — in a "(none)" bucket."""
    admin_id = str(sample_users["admin"].id)
    db_session.add(
        Personnel(
            nominal_roll_id=str(sample_personnel[0].nominal_roll_id),
            pers_no="10000011",
            rank="PTE",
            category="WOSE",
            full_name="No Subunit",
            unit="Coy A",
            created_by=admin_id,
        )
    )
    await db_session.commit()

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    body = _text(_get(client))

    # Unmarked → out: In 1, Out 1, Current 0.
    assert "(none) 0 0 0 0% 1 1 0 0% 1 1 0 0%" in body
    assert "TOTAL 1 1 0 0% 3 2 1 33% 4 3 1 25%" in body


# --- Reporting basis (issue 36) ---


async def _remap_officer_into_platoon_1(
    db_session: AsyncSession, sample_users, sample_personnel
) -> None:
    """Tag the Officer (Platoon 2 / Section 1) into Platoon 1 / Section 3."""
    admin_id = str(sample_users["admin"].id)
    nr_id = str(sample_personnel[0].nominal_roll_id)
    tagging = Tagging(label="Exercise", nominal_roll_id=nr_id, created_by=admin_id)
    db_session.add(tagging)
    await db_session.flush()
    db_session.add(
        TaggingEntry(
            tagging_id=str(tagging.id),
            personnel_id=str(sample_personnel[2].id),
            from_unit="Coy A",
            from_sub_unit_1="Platoon 2",
            from_sub_unit_2="Section 1",
            to_unit="Coy A",
            to_sub_unit_1="Platoon 1",
            to_sub_unit_2="Section 3",
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_untagged_basis_groups_by_original_allocations(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """Untagged (super-admin only) ignores the tagging overlay: the
    remapped Officer reports under his original Platoon 2 / Section 1,
    Section 3 never appears, and the unit TOTAL is allocation-independent
    (identical to the tagged report)."""
    await _remap_officer_into_platoon_1(db_session, sample_users, sample_personnel)

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    response = _get(client, basis="untagged")
    assert response.status_code == 200
    raw = _raw(response)
    assert "Untagged (original NR)" in raw  # heading names the basis
    assert "original nominal-roll" in raw  # legend explains the basis
    assert 'value="untagged" selected' in raw  # toggle reflects it
    body = _text(response)

    assert "Section 1 1 1 0 0% 0 0 0 0% 1 1 0 0%" in body  # Officer, home subunit
    assert "Section 3" not in body  # the remap target is not consulted
    assert "SUBTOTAL 0 0 0 0% 2 1 1 50% 2 1 1 50%" in body  # Platoon 1 WOSE only
    assert "SUBTOTAL 1 1 0 0% 0 0 0 0% 1 1 0 0%" in body  # Platoon 2 subtotal
    # Same grand total as the tagged report (test_tagging_overlay_regroups_rows).
    assert "TOTAL 1 1 0 0% 2 1 1 50% 3 2 1 33%" in body


@pytest.mark.asyncio
async def test_tagged_basis_selected_by_default_and_on_explicit_param(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """Default (no param) and explicit basis=tagged both apply the overlay:
    the Officer groups under his remap target, and the toggle renders
    Tagged as selected."""
    await _remap_officer_into_platoon_1(db_session, sample_users, sample_personnel)

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    for params in ({}, {"basis": "tagged"}, {"basis": "banana"}):
        response = client.get(
            "/admin", params={"date": TODAY.isoformat(), **params}
        )
        assert response.status_code == 200
        raw = _raw(response)
        assert "· Tagged" in raw
        assert 'value="tagged" selected' in raw
        body = _text(response)
        assert "Section 3 1 1 0 0% 0 0 0 0% 1 1 0 0%" in body
        assert "Platoon 2" not in body


@pytest.mark.asyncio
async def test_untagged_basis_denied_to_admins(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """A regular admin (scoped in, so the tagged report renders fine for
    them) explicitly requesting the untagged basis gets the 403 no-access
    page; their default report is unchanged tagged."""
    await _remap_officer_into_platoon_1(db_session, sample_users, sample_personnel)
    admin_id = str(sample_users["admin"].id)
    db_session.add(
        UserSubunitAssignment(
            user_id=admin_id,
            nominal_roll_id=str(sample_personnel[0].nominal_roll_id),
            sub_unit_1="Platoon 1",
            created_by=admin_id,
        )
    )
    await db_session.commit()
    await _sign_in(client, db_session, sample_users["admin"])

    response = _get(client, basis="untagged")
    assert response.status_code == 403
    assert "You do not have access to this page" in response.text
    assert "restricted to super administrators" in response.text

    # Default report: no toggle rendered, tagged basis labeled. Scoping is
    # tagging-aware: the remapped Officer now sits inside the admin's
    # Platoon 1 grant, so he reports under his (tagged) Section 3 — admin
    # reporting always follows the tagged basis.
    response = _get(client)
    assert response.status_code == 200
    assert 'name="basis"' not in _raw(response)
    assert "· Tagged" in _raw(response)
    body = _text(response)
    assert "Section 3 1 1 0 0% 0 0 0 0% 1 1 0 0%" in body
    assert "Platoon 2" not in body


# --- Access scoping ---


@pytest.mark.asyncio
async def test_admin_scoped_to_assigned_subunits(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
    sample_attendance,
):
    """A regular admin sees only assigned sub_unit_1 sections, and the
    TOTAL sums just the visible rows."""
    admin_id = str(sample_users["admin"].id)
    db_session.add(
        UserSubunitAssignment(
            user_id=admin_id,
            nominal_roll_id=str(sample_personnel[0].nominal_roll_id),
            sub_unit_1="Platoon 1",
            created_by=admin_id,
        )
    )
    await db_session.commit()
    await _sign_in(client, db_session, sample_users["admin"])

    response = _get(client)
    assert response.status_code == 200
    body = _text(response)

    assert "Platoon 1" in body
    assert "Platoon 2" not in body
    # Officer (Platoon 2) invisible: TOTAL has no Officer In.
    assert "TOTAL 0 0 0 0% 2 1 1 50% 2 1 1 50%" in body


@pytest.mark.asyncio
async def test_admin_without_assignments_gets_empty_state(
    client: TestClient,
    db_session: AsyncSession,
    sample_users,
    sample_personnel,
    sample_attendance_scope,
):
    """Deny-by-default: no assignments means no report, with guidance."""
    await _sign_in(client, db_session, sample_users["admin"])

    response = _get(client)
    assert response.status_code == 200
    body = _text(response)

    assert "no Subunit-1 assignments" in body
    assert 'class="str-total"' not in _raw(response)


# --- Feature flag ---


@pytest.mark.asyncio
async def test_flag_off_hides_nav_and_404s(
    client: TestClient, db_session: AsyncSession, sample_users, monkeypatch
):
    """FEATURE_STRENGTH off: /admin 404s with the styled disabled page for
    every role, and the sidebar loses the Unit Strength entry."""
    for settings_obj in {get_settings(), main_app.state.settings}:
        monkeypatch.setattr(settings_obj, "FEATURE_STRENGTH", False)

    sa = await _make_super_admin(db_session)
    await _sign_in(client, db_session, sa)

    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Unit Strength is switched off on this deployment" in response.text

    nr_view = client.get("/nominal-roll")
    assert nr_view.status_code == 200
    assert 'href="/admin"' not in nr_view.text
