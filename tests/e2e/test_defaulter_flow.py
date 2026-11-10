"""E2E: the defaulter release upload → dashboard annotation flow.

Drives the real UI: pick the quarter on the upload page, ingest a
release CSV, see the defaulter card land at the very top of the
dashboard, annotate a row (reason + remarks), save, reload, and find
the annotation persisted. Uses the seed helper's fallback roster names
(E2E FALLBACK PERSON / E2E SECOND PERSON), so it runs without the
gitignored fixtures.
"""

import csv
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

PENDING_HEADER = [
    "TYPE",
    "UNIT",
    "PERS",
    "RANK",
    "NAME",
    "OFFENCE TYPE",
    "OFFENCE WINDOW",
    "DEFAULT COUNT",
    "SAF264 ISSUED",
    "SCHEDULED SUMMARY TRIAL",
    "MAIL MERGE DATE",
    "DUE IN (DAYS)",
    "AMENDMENT STATUS",
    "",
    "",
    "RANK GROUP",
]
ARCHIVED_HEADER = [
    "TYPE",
    "UNIT",
    "PERS",
    "RANK",
    "NAME",
    "OFFENCE TYPE",
    "OFFENCE WINDOW",
    "DEFAULT COUNT",
    "DEFAULTER COUNT",
    "DISCIPLINARY ACTION",
    "CHARGE STATUS",
    "SCHEDULED SUMMARY TRIAL",
    "VERDICT",
    "JUSTIFICATION",
    "",
    "",
    "RANK GROUP",
]


def _write_release(tmp_path: Path) -> str:
    """A release CSV in the live file's shape, using the seeded roster."""
    path = tmp_path / "defaulter_release.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "TYPE",
                "UNIT",
                "PERS",
                "RANK",
                "NAME",
                "OFFENCE TYPE",
                "OFFENCE WINDOW",
                "DEFAULT COUNT",
                "PAST OFFENCES",
                "DUE IN (DAYS)",
            ]
        )
        writer.writerow([])
        writer.writerow(PENDING_HEADER)
        writer.writerow(
            [
                "Pending ST",
                "DK314",
                "70000000",
                "CPL",
                "E2E FALLBACK PERSON",
                "NS FIT Defaulter",
                "25/08/2025 - 24/08/2026",
                "6",
                "3",
                "20/10/2026, 10:45",
                "294",
            ]
        )
        writer.writerow([])
        writer.writerow(ARCHIVED_HEADER)
        writer.writerow(
            [
                "Archived",
                "DK314",
                "70000001",
                "PTE",
                "E2E SECOND PERSON",
                "NS FIT Defaulter",
                "03/07/2025 - 02/07/2026",
                "3",
                "1",
                "Issued SWL (Reset)",
                "SWL issued",
                "",
                "Issued SWL (Reset)",
                "N.A.",
            ]
        )
        writer.writerow([])
    return str(path)


def test_defaulter_upload_and_annotate_flow(page, e2e_server, tmp_path):
    release = _write_release(tmp_path)

    # Upload through the quarter picker.
    page.goto(f"{e2e_server}/ippt/upload")
    page.select_option("#defaulterQuarter", "3")
    page.fill("#defaulterYear", "2026")
    page.set_input_files("#defaulterFile", release)
    page.click("#defaulterUploadBtn")
    page.wait_for_selector("#defaulterResult .card")
    assert "Q3 2026 ingested" in page.inner_text("#defaulterStatus")
    assert "E2E FALLBACK PERSON" not in page.inner_text(
        "#defaulterResult"
    )  # matched names are not listed back

    # The dashboard's defaulter card now sits BELOW the filter bar:
    # after the filter/sort form, before the first roster section.
    page.goto(f"{e2e_server}/ippt/dashboard")
    card = page.locator("#defaulterCard")
    card.wait_for()
    assert card.inner_text().startswith("Defaulters — Q3 2026")
    position = page.evaluate(
        """() => {
            const card = document.getElementById('defaulterCard');
            const filters = document.querySelector('form.ippt-filters');
            const firstSection = document.querySelector('.ippt-section');
            const PRECEDING = Node.DOCUMENT_POSITION_PRECEDING;
            const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
            return {
                afterFilters: !!(filters.compareDocumentPosition(card) & FOLLOWING),
                beforeSections: !!(firstSection.compareDocumentPosition(card) & PRECEDING),
            };
        }"""
    )
    assert position["afterFilters"], "defaulter card must come after the filter bar"
    assert position["beforeSections"], "defaulter card must precede the roster sections"
    assert "E2E FALLBACK PERSON" in card.inner_text()
    assert "1" in card.inner_text()  # one pending row; archived stays hidden
    assert "E2E SECOND PERSON" not in card.inner_text()

    # It is a <details>, open by default; the summary header collapses
    # and re-opens the table without any page reload.
    assert card.locator("table").is_visible()
    card.locator("summary").click()
    assert not card.locator("table").is_visible()
    card.locator("summary").click()
    assert card.locator("table").is_visible()

    # Annotate the row and save; the button's "Saved ✓" flip is the
    # completion signal.
    row = card.locator("tbody tr").first
    assert row.locator("td").nth(2).inner_text().startswith("E2E FALLBACK PERSON")
    row.locator("select.defaulter-reason").select_option("hsp")
    row.locator("input.defaulter-remarks").fill("counselled 08 Nov")
    row.locator("button.defaulter-save").click()
    card.locator("button.defaulter-save", has_text="Saved ✓").wait_for()

    # Reload: the annotation persisted.
    page.reload()
    card = page.locator("#defaulterCard")
    card.wait_for()
    row = card.locator("tbody tr").first
    assert row.locator("select.defaulter-reason").input_value() == "hsp"
    assert row.locator("input.defaulter-remarks").input_value() == "counselled 08 Nov"
