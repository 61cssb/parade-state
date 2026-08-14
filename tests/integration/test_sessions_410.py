"""Sessions endpoints are gone (410 Gone).

Sessions were removed in the attendance rework (issue #4): attendance is now
AM/PM hardcoded and scoped to an NR/Tagging. The routes remain as signposts.
"""

from fastapi.testclient import TestClient


def test_get_sessions_returns_410(client: TestClient):
    """Any GET under /api/v1/sessions returns 410 Gone."""
    response = client.get("/api/v1/sessions/", params={"user_id": "u", "user_role": "admin"})
    assert response.status_code == 410


def test_create_session_returns_410(client: TestClient):
    """POST to /api/v1/sessions returns 410 Gone."""
    response = client.post(
        "/api/v1/sessions/",
        params={"user_id": "u", "user_role": "admin"},
        json={"grouping_id": "x", "date": "2026-01-01", "session_type": "AM"},
    )
    assert response.status_code == 410


def test_specific_session_path_returns_410(client: TestClient):
    """Deep paths under /api/v1/sessions also return 410."""
    response = client.delete(
        "/api/v1/sessions/some-id",
        params={"user_id": "u", "user_role": "super_admin"},
    )
    assert response.status_code == 410
