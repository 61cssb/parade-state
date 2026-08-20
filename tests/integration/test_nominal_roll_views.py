"""Tests for the nominal roll browser HTML view.

The cell editor is client-side JS that stages edits locally (surviving
refreshes via localStorage) and applies them with one PATCH
/api/v1/personnel/{id} per person (the redirect-to-tagging behaviour is
covered in test_personnel_api.py); these tests pin the view wiring —
editable cells, the embedded suggestion lists, and the staged-edit
Apply/Discard bar for super-admins, plain read-only cells for everyone else.

Note: the auth helper is called directly inside the handler (not via
Depends()), so we monkeypatch the module-level reference to inject a user.
"""

import pytest
from fastapi.testclient import TestClient

from parade_state.models import User


def test_nominal_roll_redirects_when_unauthenticated(client: TestClient):
    """Unauthenticated /nominal-roll redirects to login (302)."""
    response = client.get("/nominal-roll", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_nominal_roll_super_admin_cell_editor_wiring(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_users,
    db_session,
    monkeypatch,
):
    """Super-admins get clickable cells and the custom suggestion panel data.

    The panel replaced the native <datalist> popup (whose placement is
    browser-controlled): options are embedded as an EDIT_OPTIONS map and no
    <datalist> elements remain. Blank ("clear value") picks are limited to
    sub-units 2/3 via BLANK_FIELDS.
    """
    from parade_state.web import nominal_roll as web_nominal_roll

    super_admin = User(
        email="super-nr@example.com",
        name="Super Admin",
        role="super_admin",
        status="active",
    )
    db_session.add(super_admin)
    await db_session.commit()

    async def _fake_current_user(_request):
        return super_admin

    monkeypatch.setattr(web_nominal_roll, "get_current_user_optional", _fake_current_user)

    response = client.get(
        "/nominal-roll", params={"nominal_roll_id": str(sample_nominal_roll.id)}
    )
    assert response.status_code == 200
    # Editable cells carry the field the PATCH redirect keys off.
    assert 'data-field="unit"' in response.text
    assert 'data-field="sub_unit_1"' in response.text
    assert 'data-field="sub_unit_3"' in response.text
    # Custom panel: embedded option map, no native datalist popups.
    assert "EDIT_OPTIONS" in response.text
    assert "<datalist" not in response.text
    assert "Coy A" in response.text  # unit suggestions (sample personnel)
    assert "Platoon 2" in response.text  # sub-unit 1 suggestions
    assert "BLANK_FIELDS" in response.text


@pytest.mark.asyncio
async def test_nominal_roll_staged_edits_wiring(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_users,
    db_session,
    monkeypatch,
):
    """Super-admins get the staged-edit flow: edits are held client-side
    (darker-yellow pending cells, per-roll localStorage draft) until the
    floating bar's Apply sends one PATCH per person, or Discard reverts.
    """
    from parade_state.web import nominal_roll as web_nominal_roll

    super_admin = User(
        email="super-stage@example.com",
        name="Super Admin",
        role="super_admin",
        status="active",
    )
    db_session.add(super_admin)
    await db_session.commit()

    async def _fake_current_user(_request):
        return super_admin

    monkeypatch.setattr(web_nominal_roll, "get_current_user_optional", _fake_current_user)

    response = client.get(
        "/nominal-roll", params={"nominal_roll_id": str(sample_nominal_roll.id)}
    )
    assert response.status_code == 200
    # Per-roll localStorage draft key, so staged edits survive a refresh.
    assert "ps:nr-edits:" in response.text
    # Staging instead of instant saves.
    assert "stageCellEdit" in response.text
    assert "saveCellEdit" not in response.text
    # Floating bar with Apply/Discard.
    assert "pending-bar" in response.text
    assert "applyStaged" in response.text
    assert "discardStaged" in response.text
    # Pending cells are a darker yellow than the saved-changes row (#fef3c7).
    assert ".cell-edit.pending" in response.text
    assert "#fcd34d" in response.text


@pytest.mark.asyncio
async def test_nominal_roll_read_only_for_non_super_admins(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_attendance_scope,
    sample_users,
    monkeypatch,
):
    """Non-super-admins get a plain read-only table — no editor markup."""
    from parade_state.web import nominal_roll as web_nominal_roll

    async def _fake_current_user(_request):
        return sample_users["admin"]

    monkeypatch.setattr(web_nominal_roll, "get_current_user_optional", _fake_current_user)

    response = client.get(
        "/nominal-roll", params={"nominal_roll_id": str(sample_nominal_roll.id)}
    )
    assert response.status_code == 200
    assert "John Doe" in response.text  # roster still renders
    assert "data-field=" not in response.text
    assert "EDIT_OPTIONS" not in response.text
    # No staged-edit machinery either — nothing to stage without the editor.
    # (The inert CSS ships for everyone; the JS wiring is what matters.)
    assert "stageCellEdit" not in response.text
    assert "ps:nr-edits" not in response.text


@pytest.mark.asyncio
async def test_nominal_roll_shows_callup_column_for_all_statuses(
    client: TestClient,
    sample_nominal_roll,
    sample_personnel,
    sample_users,
    db_session,
    monkeypatch,
):
    """The NR table shows every callup status — the NR is the management
    surface; only the attendance view filters (issue 06)."""
    from parade_state.web import nominal_roll as web_nominal_roll
    from parade_state.models import User

    sample_personnel[0].callup_status = "Deferred"
    sample_personnel[0].remarks = "Course till Friday"
    db_session.add(sample_personnel[0])
    await db_session.commit()

    super_admin = User(
        email="super-callup-nr@example.com",
        name="Super Admin",
        role="super_admin",
        status="active",
    )
    db_session.add(super_admin)
    await db_session.commit()

    async def _fake_current_user(_request):
        return super_admin

    monkeypatch.setattr(web_nominal_roll, "get_current_user_optional", _fake_current_user)

    response = client.get(
        "/nominal-roll", params={"nominal_roll_id": str(sample_nominal_roll.id)}
    )
    assert response.status_code == 200
    assert "Callup" in response.text  # column header
    # All six options are offered to admins/super-admins.
    for status in ("Called Up", "Deferred", "Disrupted", "MR", "Age Limit", "Other"):
        assert f">{status}</option>" in response.text
    # Remarks come from the personnel column, not extra_fields.
    assert "Course till Friday" in response.text
    # Inline-edit wiring: immediate PATCH handlers for admins and above.
    assert "onCallupChange" in response.text
    assert "onPersonnelRemarksChange" in response.text


@pytest.mark.asyncio
async def test_nominal_roll_redirects_non_admins(
    client: TestClient,
    sample_nominal_roll,
    sample_users,
    monkeypatch,
):
    """The viewer role is deferred — non-admins get the no-access redirect
    (so callup editing being admin+ effectively covers every page viewer)."""
    from parade_state.web import nominal_roll as web_nominal_roll

    async def _fake_current_user(_request):
        return sample_users["user"]

    monkeypatch.setattr(web_nominal_roll, "get_current_user_optional", _fake_current_user)

    response = client.get(
        "/nominal-roll",
        params={"nominal_roll_id": str(sample_nominal_roll.id)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/auth/no-access" in response.headers["location"]
