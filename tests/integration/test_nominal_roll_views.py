"""Tests for the nominal roll browser HTML view.

The cell editor is client-side JS over PATCH /api/v1/personnel/{id} (the
redirect-to-tagging behaviour is covered in test_personnel_api.py); these
tests pin the view wiring — editable cells plus the embedded suggestion
lists for super-admins, plain read-only cells for everyone else.

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
async def test_nominal_roll_read_only_for_regular_users(
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
        return sample_users["user"]

    monkeypatch.setattr(web_nominal_roll, "get_current_user_optional", _fake_current_user)

    response = client.get(
        "/nominal-roll", params={"nominal_roll_id": str(sample_nominal_roll.id)}
    )
    assert response.status_code == 200
    assert "John Doe" in response.text  # roster still renders
    assert "data-field=" not in response.text
    assert "EDIT_OPTIONS" not in response.text
