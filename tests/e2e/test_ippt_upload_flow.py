"""E2E: the IPPT upload → dashboard → window flow in a real browser.

Drives system Chrome (via Playwright) against a real uvicorn server on a
freshly migrated + seeded database. The canonical fixtures upload covers
the full client-side validation (six canonical files) and the server's
atomic ingest; the dashboard and window assertions pin what the feature
is for: seeing who needs chasing.

The database is rebuilt per test (see conftest), so repeated runs and
re-uploads always start from the same state.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "ippt"


def _fixture_files() -> list[str]:
    return sorted(str(path) for path in FIXTURES.glob("*.csv"))


def _upload(page, base_url: str, files: list[str]) -> None:
    """Upload files through the real UI and wait for the request to finish.

    The upload button is disabled while the request is in flight and
    re-enabled in a ``finally`` — a reliable completion signal.
    """
    page.goto(f"{base_url}/ippt/upload")
    page.set_input_files("#ipptFiles", files)
    page.click("#uploadBtn")
    page.wait_for_function("document.querySelector('#uploadBtn').disabled === false")


def test_client_side_rejects_incomplete_selection(page, e2e_server):
    files = _fixture_files()
    if not files:
        pytest.skip("canonical IPPT fixtures not present — /fixtures is gitignored")

    page.goto(f"{e2e_server}/ippt/upload")
    page.set_input_files("#ipptFiles", files[:2])
    page.click("#uploadBtn")
    status = page.inner_text("#uploadStatus")
    assert "exactly the six report files" in status


def test_upload_fixtures_and_read_dashboard(page, e2e_server):
    files = _fixture_files()
    if not files:
        pytest.skip("canonical IPPT fixtures not present — /fixtures is gitignored")

    _upload(page, e2e_server, files)
    status = page.inner_text("#uploadStatus")
    assert "2026-09-11 ingested" in status
    result = page.inner_text("#uploadResult")
    assert "284 rows" in result
    assert "0 quarantined" in result

    # The dashboard reflects the ingest.
    page.click("text=Open the dashboard")
    page.wait_for_selector("text=3-month tier")
    body = page.content()
    assert "284" in body  # tracked-servicemen counter
    assert "LEW EE KENT" in body  # the most urgent failure (4 days left)
    assert "9-month tier" in body
    assert "Snapshot history" in body

    # Through to a per-person window page via a roster link.
    page.click("a:has-text('LEW EE KENT')")
    page.wait_for_selector("text=Trajectory")
    detail = page.content()
    assert "Attempted — failed (can re-attempt)" in detail
    assert "Health screening (FFI)" in detail


def test_reupload_replaces_same_report_date(page, e2e_server):
    files = _fixture_files()
    if not files:
        pytest.skip("canonical IPPT fixtures not present — /fixtures is gitignored")

    _upload(page, e2e_server, files)
    assert "ingested" in page.inner_text("#uploadStatus")

    # Same six files again: replace-on-re-ingest keeps the dashboard stable.
    _upload(page, e2e_server, files)
    assert "replaced the previous ingest" in page.inner_text("#uploadStatus")

    page.goto(f"{e2e_server}/ippt/dashboard")
    page.wait_for_selector("text=Snapshot history")
    assert "284" in page.content()
