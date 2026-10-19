"""Unit tests: the dashboard XLSX export (utils.ippt_export) — a pure
function over the dashboard payload's entry dicts, no database involved.

The workbook contract pinned here: five tabs in dashboard order, the
dashboard's exact cell text (FIT/award parentheticals, Yes/No/- booked,
Pending/Fit/NA screening, ``-`` blanks), and "Window ends" as real date
cells. The route behaviour (auth, media type, filename) is covered in
tests/behavioral/test_ippt_pages.py."""

import io
from datetime import datetime

from openpyxl import load_workbook

from parade_state.utils.ippt_export import build_dashboard_workbook


def entry(
    name: str,
    *,
    rank: str = "PTE",
    sub_unit: str = "BN HQ",
    state_label: str = "Not attempted",
    fit_sessions: int | None = None,
    ippt_status: str | None = None,
    booked: bool | None = None,
    ffi: str | None = None,
    window_end: str | None = None,
    days_to_close: int | None = None,
    highlighted: bool = False,
    fulfilled: bool = False,
) -> dict:
    return {
        "rank": rank,
        "full_name": name,
        "sub_unit": sub_unit,
        "state_label": state_label,
        "fit_sessions": fit_sessions,
        "ippt_status": ippt_status,
        "booked": booked,
        "ffi": ffi,
        "window_end": window_end,
        "days_to_close": days_to_close,
        "highlighted": highlighted,
        "fulfilled": fulfilled,
    }


def workbook_bytes(servicemen: list[dict], **sections) -> bytes:
    return build_dashboard_workbook(
        {
            "servicemen": servicemen,
            "tiers": {
                "tier_3": sections.get("tier_3", []),
                "tier_6": sections.get("tier_6", []),
                "tier_9": sections.get("tier_9", []),
            },
            "fulfilled": sections.get("fulfilled", []),
        }
    )


def load(servicemen: list[dict], **sections):
    return load_workbook(io.BytesIO(workbook_bytes(servicemen, **sections)))


def sheet_rows(sheet) -> list[list]:
    return [[cell.value for cell in row] for row in sheet.iter_rows()]


class TestTabLayout:
    def test_five_tabs_in_dashboard_order(self):
        book = load([])
        assert book.sheetnames == [
            "3 months",
            "6 months",
            "9 months",
            "On track",
            "Fulfilled",
        ]

    def test_headers_mirror_the_page_columns(self):
        book = load([])
        tier_header = [cell.value for cell in book["3 months"][1]]
        assert tier_header == [
            "Rank",
            "Name",
            "Sub-unit",
            "State",
            "Booked",
            "FFI",
            "Window ends",
            "Days left",
        ]
        ontrack_header = [cell.value for cell in book["On track"][1]]
        assert ontrack_header == ["Rank", "Name", "Sub-unit", "State", "Window ends"]
        fulfilled_header = [cell.value for cell in book["Fulfilled"][1]]
        assert fulfilled_header == ["Rank", "Name", "Sub-unit", "How"]

    def test_empty_dashboard_writes_header_only_sheets(self):
        book = load([])
        for title in book.sheetnames:
            assert book[title].max_row == 1

    def test_tier_tabs_come_from_the_payloads_tiers(self):
        alpha = entry("ALPHA", window_end="2027-07-08", days_to_close=300)
        book = load([], tier_3=[alpha])
        rows = sheet_rows(book["3 months"])
        assert len(rows) == 2
        assert rows[1][1] == "ALPHA"
        assert (
            sheet_rows(book["6 months"])
            == sheet_rows(book["9 months"])
            == [[cell.value for cell in book["6 months"][1]]]
        )


class TestTierCellContents:
    def test_state_carries_the_fit_parenthetical_like_the_page(self):
        runner = entry(
            "RUNNER",
            state_label="Voluntary NS FIT in progress",
            fit_sessions=3,
            window_end="2027-03-15",
            days_to_close=147,
        )
        row = sheet_rows(load([], tier_3=[runner])["3 months"])[1]
        assert row == [
            "PTE",
            "RUNNER",
            "BN HQ",
            "Voluntary NS FIT in progress (3 FIT)",
            "-",  # booked unknown
            "-",  # FFI not recorded
            datetime(2027, 3, 15),  # real date cell
            147,
        ]

    def test_booked_tri_state_and_ffi_renderings(self):
        alpha = entry("ALPHA", booked=True, ffi="pending")
        bravo = entry("BRAVO", booked=False, ffi="fit")
        charlie = entry("CHARLIE", booked=None, ffi="na")
        book = load([], tier_3=[alpha, bravo, charlie])
        booked_col = [row[4] for row in sheet_rows(book["3 months"])[1:]]
        ffi_col = [row[5] for row in sheet_rows(book["3 months"])[1:]]
        assert booked_col == ["Yes", "No", "-"]
        assert ffi_col == ["Pending", "Fit", "Na"]

    def test_window_ends_is_a_date_cell_with_iso_display(self):
        runner = entry("RUNNER", window_end="2027-03-15", days_to_close=147)
        sheet = load([], tier_3=[runner])["3 months"]
        cell = sheet.cell(row=2, column=7)
        assert cell.value == datetime(2027, 3, 15)
        assert cell.number_format == "yyyy-mm-dd"

    def test_blank_window_still_shows_the_dash(self):
        stale = entry("STALE", window_end=None, days_to_close=None)
        row = sheet_rows(load([], tier_3=[stale])["3 months"])[1]
        assert row[6] == "-" and row[7] == "-"


class TestOnTrackAndFulfilled:
    def test_on_track_is_the_not_highlighted_unfulfilled_set(self):
        ontrack = entry("ECHO", window_end="2027-11-02", days_to_close=380)
        highlighted = entry("ALPHA", highlighted=True)
        fulfilled = entry("DELTA", fulfilled=True)
        rows = sheet_rows(load([ontrack, highlighted, fulfilled])["On track"])
        assert [row[1] for row in rows[1:]] == ["ECHO"]
        assert rows[1] == [
            "PTE",
            "ECHO",
            "BN HQ",
            "Not attempted",
            datetime(2027, 11, 2),
        ]

    def test_fulfilled_how_keeps_the_award_parenthetical(self):
        gold = entry("GOLD", state_label="Met — IPPT award", ippt_status="Gold")
        waived = entry("WAIVED", state_label="Waived — special handling")
        rows = sheet_rows(load([gold, waived], fulfilled=[gold, waived])["Fulfilled"])
        assert rows[1] == ["PTE", "GOLD", "BN HQ", "Met — IPPT award (Gold)"]
        assert rows[2] == ["PTE", "WAIVED", "BN HQ", "Waived — special handling"]


class TestWorkbookHygiene:
    def test_header_row_is_bold_and_frozen(self):
        sheet = load([], tier_3=[entry("ALPHA")])["3 months"]
        assert all(cell.font.bold for cell in sheet[1])
        assert sheet.freeze_panes == "A2"

    def test_bytes_round_trip_through_openpyxl(self):
        payload = workbook_bytes([], tier_3=[entry("ALPHA", window_end="2027-07-08")])
        assert isinstance(payload, bytes) and payload[:2] == b"PK"  # a zip: xlsx


def test_iso_date_never_appears_as_text():
    """The window date is a real date cell everywhere it appears — no
    tab ships it as a text string (decided 2026-10-19)."""
    dated = entry("ALPHA", window_end="2027-07-08", days_to_close=300)
    book = load([dated], tier_3=[dated])
    tier_row = sheet_rows(book["3 months"])[1]
    assert tier_row[6] == datetime(2027, 7, 8)  # "Window ends" column
    ontrack_row = sheet_rows(book["On track"])[1]
    assert ontrack_row[-1] == datetime(2027, 7, 8)
