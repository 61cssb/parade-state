"""IPPT report parsing/derivation units (docs/IPPT_MONITORING.md §2–§6.4).

Pins the ingestion contract exactly: filename grammar, header-name
mapping per family, the ``-`` null sentinel, the §6.2 state matrix
(including the unobserved combinations that must quarantine), window
date derivation, and the §6.4 tier rules. The final acceptance test
parses the real canonical fixtures and asserts the §2.4 per-file
observation counts.
"""

import io
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from parade_state.utils.ippt_reports import (
    FilenameError,
    ReportFileError,
    derive_state,
    is_all_tabs_filename,
    months_between,
    normalize_name,
    parse_all_tabs_filename,
    parse_all_tabs_workbook,
    parse_filename,
    parse_report_file,
    tier_band,
    tier_highlighted,
    window_dates,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IPPT_FIXTURES = REPO_ROOT / "fixtures" / "ippt"


# ============================================================================
# Filenames
# ============================================================================


def test_parse_filename_canonical():
    assert parse_filename("IPPT_COMPLETED_20260911.csv") == (
        "ippt_completed",
        date(2026, 9, 11),
    )
    assert parse_filename("NSFIT_NOT_STARTED_20260911.csv") == (
        "nsfit_not_started",
        date(2026, 9, 11),
    )


def test_parse_filename_accepts_any_case():
    assert parse_filename("ippt_failed_20260911.CSV")[0] == "ippt_failed"


@pytest.mark.parametrize(
    "name",
    [
        "IPPT_COMPLETED.csv",  # no date
        "IPPT_COMPLETED_2026.csv",
        "ippt_completed_20260911.txt",
        "REPORT_20260911.csv",
        "IPPT_UNKNOWN_20260911.csv",
        "random.csv",
    ],
)
def test_parse_filename_rejects_non_canonical(name):
    with pytest.raises(FilenameError):
        parse_filename(name)


# ============================================================================
# Row parsing per family
# ============================================================================


COMPLETED_A = (
    "Rank,Name,Unit,Sub-unit,IPPT status,FIT sessions completed\n"
    'CPL   ,"TAN KAI KEONG, DERRICK",DK314,BN HQ,Gold,6\n'
    "PTE   ,NOBODY DISCUSSED,DK314,MEDICAL COY,Complete FIT,9\n"
    "3SG   ,WAIVED PERSON,DK314,SECURITY COY,-,-\n"
)


def test_parse_family_a_maps_by_header_and_strips_rank():
    parsed = parse_report_file("IPPT_COMPLETED_20260911.csv", COMPLETED_A)
    assert [r.state for r in parsed.rows] == [
        "met_award",
        "met_voluntary_fit",
        "waived",
    ]
    first = parsed.rows[0]
    assert first.rank == "CPL"  # trailing padding stripped
    assert first.full_name == "TAN KAI KEONG, DERRICK"  # quoted comma kept
    assert first.fit_sessions == 6


def test_parse_sentinel_dash_is_none_everywhere():
    parsed = parse_report_file("IPPT_COMPLETED_20260911.csv", COMPLETED_A)
    waived = parsed.rows[2]
    assert waived.ippt_status is None
    assert waived.fit_sessions is None


def test_parse_family_b_order_independent():
    # Same fields, different column order (§2.2) — both must parse alike.
    failed_order = (
        "Rank,Name,Unit,Sub-unit,Window close,IPPT status,FIT sessions completed,"
        "Reminder sent,Last sent\n"
        "CPL   ,A PERSON,DK314,MEDICAL COY,4,Fail,8,-,-\n"
    )
    inprog_order = (
        "Rank,Name,Unit,Sub-unit,Window close,FIT sessions completed,IPPT status,"
        "Reminder sent,Last sent\n"
        "CFC   ,B PERSON,DK314,MEDICAL COY,55,5,-,-,-\n"
    )
    failed = parse_report_file("IPPT_FAILED_20260911.csv", failed_order)
    inprog = parse_report_file("NSFIT_IN_PROGRESS_20260911.csv", inprog_order)

    assert failed.rows[0].state == "failed_can_reattempt"
    assert failed.rows[0].window_close_days == 4
    assert failed.rows[0].fit_sessions == 8
    assert failed.rows[0].reminder_sent is None  # '-' sentinel
    assert inprog.rows[0].state == "mandatory_fit_in_progress"
    assert inprog.rows[0].ippt_status is None
    assert inprog.rows[0].fit_sessions == 5


def test_parse_family_c_booked_and_ffi():
    content = (
        "Rank,Name,Unit,Sub-unit,Window close,Booked Status,FFI,Reminder sent,"
        "Last sent\n"
        "PTE   ,A PERSON,DK314,NON-ESTAB,19,No,NA,-,-\n"
        "PTE   ,B PERSON,DK314,S4 BR/HQ COY,22,Yes,Pending,-,-\n"
    )
    parsed = parse_report_file("IPPT_NOT_ATTEMPTED_20260911.csv", content)
    assert parsed.rows[0].booked is False
    assert parsed.rows[0].ffi == "na"
    assert parsed.rows[1].booked is True
    assert parsed.rows[1].ffi == "pending"


def test_parse_bad_rows_quarantine_not_reject():
    content = (
        "Rank,Name,Unit,Sub-unit,Window close,IPPT status,FIT sessions completed,"
        "Reminder sent,Last sent\n"
        "CPL   ,GOOD ROW,DK314,BN HQ,12,Fail,-,-,-\n"
        "CPL   ,BAD CLOSE,DK314,BN HQ,999,Fail,-,-,-\n"
        "CPL   ,BAD FIT,DK314,BN HQ,12,Fail,42,-,-\n"
        "      ,NO RANK,DK314,BN HQ,12,Fail,-,-,-\n"
    )
    parsed = parse_report_file("IPPT_FAILED_20260911.csv", content)
    assert len(parsed.rows) == 1
    assert len(parsed.quarantined) == 3
    reasons = " | ".join(q.reason for q in parsed.quarantined)
    assert "outside [0, 366]" in reasons
    assert "outside [0, 10]" in reasons
    assert "missing Rank" in reasons


def test_parse_missing_required_header_rejects_file():
    with pytest.raises(ReportFileError):
        parse_report_file(
            "IPPT_COMPLETED_20260911.csv",
            "Rank,Name,Unit,Sub-unit\nCPL,X,DK314,BN HQ\n",
        )


def test_parse_empty_file_rejects():
    with pytest.raises(ReportFileError):
        parse_report_file("IPPT_COMPLETED_20260911.csv", "")


# ============================================================================
# Excel (.xlsx) uploads
# ============================================================================


def _xlsx_bytes(*rows: list) -> bytes:
    """An in-memory one-sheet workbook holding the given cell values."""
    buffer = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None  # a new Workbook always has one sheet
    for row in rows:
        sheet.append(row)
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_filename_xlsx_canonical():
    assert parse_filename("IPPT_COMPLETED_20260911.xlsx") == (
        "ippt_completed",
        date(2026, 9, 11),
    )
    assert parse_filename("nsfit_in_progress_20260911.XLSX")[0] == ("nsfit_in_progress")
    with pytest.raises(FilenameError):
        parse_filename("IPPT_COMPLETED_20260911.xls")  # legacy format stays out


def test_parse_xlsx_equivalent_to_csv():
    content = _xlsx_bytes(
        ["Rank", "Name", "Unit", "Sub-unit", "IPPT status", "FIT sessions completed"],
        ["CPL   ", "TAN KAI KEONG, DERRICK", "DK314", "BN HQ", "Gold", 6],
        ["3SG   ", "WAIVED PERSON", "DK314", "SECURITY COY", "-", "-"],
    )
    parsed = parse_report_file("IPPT_COMPLETED_20260911.xlsx", content)
    assert [r.state for r in parsed.rows] == ["met_award", "waived"]
    first = parsed.rows[0]
    assert first.rank == "CPL"  # trailing padding stripped
    assert first.full_name == "TAN KAI KEONG, DERRICK"
    assert first.fit_sessions == 6  # numeric cell, read like the CSV "6"


def test_parse_xlsx_float_cells_render_without_noise():
    content = _xlsx_bytes(
        [
            "Rank",
            "Name",
            "Unit",
            "Sub-unit",
            "Window close",
            "IPPT status",
            "FIT sessions completed",
            "Reminder sent",
            "Last sent",
        ],
        ["CPL   ", "A PERSON", "DK314", "MEDICAL COY", 12.0, "Fail", 8.0, "-", "-"],
    )
    parsed = parse_report_file("IPPT_FAILED_20260911.xlsx", content)
    row = parsed.rows[0]
    assert row.window_close_days == 12  # 12.0 must not arrive as "12.0"
    assert row.fit_sessions == 8


def test_parse_xlsx_blank_cell_is_not_the_null_sentinel():
    # An empty Excel cell behaves like an empty CSV field (quarantine) —
    # only the explicit '-' sentinel means null (§2.3); a blank status
    # must never silently read as a waived fulfilment.
    content = _xlsx_bytes(
        ["Rank", "Name", "Unit", "Sub-unit", "IPPT status", "FIT sessions completed"],
        ["PTE   ", "BLANK STATUS", "DK314", "BN HQ", None, None],
    )
    parsed = parse_report_file("IPPT_COMPLETED_20260911.xlsx", content)
    assert parsed.rows == []
    assert len(parsed.quarantined) == 1
    assert "''" in parsed.quarantined[0].reason  # the empty field, not null


def test_parse_xlsx_trailing_blank_cells_and_rows_skipped():
    content = _xlsx_bytes(
        ["Rank", "Name", "Unit", "Sub-unit", "IPPT status", "FIT sessions completed"],
        ["PTE   ", "ONE PERSON", "DK314", "BN HQ", "Pass", "-", None, None],
        [None, None, None],
    )
    parsed = parse_report_file("IPPT_COMPLETED_20260911.xlsx", content)
    assert len(parsed.rows) == 1
    assert parsed.quarantined == []


def test_parse_xlsx_missing_header_rejects_file():
    content = _xlsx_bytes(["Rank", "Name"], ["CPL", "X"])
    with pytest.raises(ReportFileError):
        parse_report_file("IPPT_COMPLETED_20260911.xlsx", content)


def test_parse_xlsx_unreadable_bytes_reject_file():
    with pytest.raises(ReportFileError):
        parse_report_file("IPPT_COMPLETED_20260911.xlsx", b"definitely not a zip")


# ============================================================================
# State derivation (§6.2)
# ============================================================================


@pytest.mark.parametrize(
    ("kind", "status", "fit", "expected"),
    [
        # *_COMPLETED: designation authoritative; '-' = waived.
        ("ippt_completed", "Pass", None, "met_award"),
        ("ippt_completed", "Pass with Incentive", 9, "met_award"),
        ("ippt_completed", "Silver", None, "met_award"),
        ("ippt_completed", "Gold", 6, "met_award"),
        ("ippt_completed", "Complete FIT", 9, "met_voluntary_fit"),
        ("ippt_completed", None, None, "waived"),
        ("ippt_completed", None, 2, "waived"),  # residual count is incidental
        ("nsfit_completed", "Pass", None, "met_mandatory_fit"),
        ("nsfit_completed", "Pass with Incentive", 1, "met_mandatory_fit"),
        # Unobserved status shapes (§5): membership is authoritative
        # either way — never quarantined, never guessed by FIT count.
        ("nsfit_completed", "Complete FIT", 9, "met_mandatory_fit"),
        ("nsfit_completed", None, None, "met_mandatory_fit"),
        # IPPT_FAILED
        ("ippt_failed", "Fail", 8, "failed_can_reattempt"),
        ("ippt_failed", "Fail", None, "failed_can_reattempt"),
        ("ippt_failed", None, 1, "voluntary_fit_in_progress"),
        ("ippt_failed", None, 9, "voluntary_fit_in_progress"),
        # Domain call (2026-11-03): no attempt, no FIT progress this
        # window — the tab caught him for prior-window history.
        ("ippt_failed", None, 0, "ippt_not_attempted"),
        # NSFIT_IN_PROGRESS
        ("nsfit_in_progress", None, 5, "mandatory_fit_in_progress"),
        ("nsfit_in_progress", "Fail", None, "mandatory_fit_failed_attempt"),
        # Family C
        ("ippt_not_attempted", None, None, "ippt_not_attempted"),
        ("nsfit_not_started", None, None, "nsfit_not_started"),
    ],
)
def test_derive_state_known_combinations(kind, status, fit, expected):
    assert derive_state(kind, status, fit) == expected


@pytest.mark.parametrize(
    ("kind", "status", "fit"),
    [
        ("ippt_completed", "Fail", None),  # unobserved in COMPLETED
        ("nsfit_in_progress", None, None),  # unobserved
        ("nsfit_in_progress", "Pass", 3),  # award would be COMPLETED
        ("ippt_failed", None, None),  # neither attempt nor FIT observed
        ("ippt_failed", None, 10),  # above the FIT range — still unexplained
        ("ippt_failed", "Pass", None),  # a pass would be COMPLETED
    ],
)
def test_derive_state_unrecognized_combinations_quarantine(kind, status, fit):
    assert derive_state(kind, status, fit) is None


# ============================================================================
# Windows, months, tiers (§6.1, §6.4)
# ============================================================================


def test_window_dates_derive_birthday_start():
    start, end = window_dates(date(2026, 9, 11), 12)
    assert end == date(2026, 9, 23)
    assert start == date(2025, 9, 24)  # (end + 1 day) - 1 year


def test_window_dates_feb29_birthday_settles_on_feb28():
    # window_end 2028-03-01 → birthday would be 2027-03-02? No: the Feb-29
    # case is window_end Feb 29 of a leap year: (end + 1 day) - 1 year lands
    # on Mar 1 of the previous year when the birthday is Feb 29.
    start, end = window_dates(date(2027, 12, 31), 60)  # end = 2028-02-29
    assert end == date(2028, 2, 29)
    assert start == date(2027, 3, 1)


def test_months_between_floors_calendar_months():
    assert months_between(date(2025, 9, 24), date(2026, 9, 24)) == 12
    assert months_between(date(2025, 9, 24), date(2026, 9, 23)) == 11
    assert months_between(date(2026, 1, 31), date(2026, 2, 28)) == 0
    assert months_between(date(2026, 1, 1), date(2026, 1, 1)) == 0
    assert months_between(date(2026, 6, 1), date(2026, 1, 1)) == 0  # clamped


def test_tier_band_by_months_to_close():
    # Bands key on months REMAINING before the window close; tier_3 is
    # the most urgent (window about to close), tier_9 the least. At
    # exactly N months before close, the "N months" tier applies.
    assert tier_band(10) is None
    assert tier_band(11) is None
    assert tier_band(9) == "tier_9"
    assert tier_band(8) == "tier_9"
    assert tier_band(7) == "tier_9"
    assert tier_band(6) == "tier_6"
    assert tier_band(5) == "tier_6"
    assert tier_band(4) == "tier_6"
    assert tier_band(3) == "tier_3"
    assert tier_band(2) == "tier_3"
    assert tier_band(0) == "tier_3"


def test_tier9_least_urgent_highlights_only_unstarted_without_prep():
    # Not-attempted + not booked → highlighted, any FFI.
    assert tier_highlighted("tier_9", "ippt_not_attempted", False, "na")
    assert tier_highlighted("tier_9", "ippt_not_attempted", False, "fit")
    # Booked but screening pending → highlighted (cannot book until clear).
    assert tier_highlighted("tier_9", "ippt_not_attempted", True, "pending")
    # Booked + screening ok (fit or na) → satisfactory.
    assert not tier_highlighted("tier_9", "ippt_not_attempted", True, "fit")
    assert not tier_highlighted("tier_9", "ippt_not_attempted", True, "na")
    # Any other state counts as preparatory action.
    assert not tier_highlighted("tier_9", "failed_can_reattempt", None, None)
    assert not tier_highlighted("tier_9", "voluntary_fit_in_progress", None, 3)
    assert not tier_highlighted("tier_9", "nsfit_not_started", False, "na")
    assert not tier_highlighted("tier_9", "waived", None, None)


def test_tier6_requires_real_progress_or_post_screening_booking():
    assert not tier_highlighted("tier_6", "failed_can_reattempt", None, None)
    assert not tier_highlighted("tier_6", "met_award", None, None)
    assert not tier_highlighted("tier_6", "waived", None, None)
    # "booked IPPT after FFI": booked + fit is the only not-attempted pass.
    assert not tier_highlighted("tier_6", "ippt_not_attempted", True, "fit")
    # na + booked without an attempt is highlighted (cautious default).
    assert tier_highlighted("tier_6", "ippt_not_attempted", True, "na")
    assert tier_highlighted("tier_6", "ippt_not_attempted", True, "pending")
    assert tier_highlighted("tier_6", "ippt_not_attempted", False, "fit")
    # Incomplete FIT routes are chased at 6 months, mandatory included.
    assert tier_highlighted("tier_6", "voluntary_fit_in_progress", None, 4)
    assert tier_highlighted("tier_6", "mandatory_fit_in_progress", None, 2)
    assert tier_highlighted("tier_6", "mandatory_fit_failed_attempt", None, None)
    assert tier_highlighted("tier_6", "nsfit_not_started", True, "fit")


def test_tier3_most_urgent_highlights_everything_unfulfilled():
    for state in (
        "ippt_not_attempted",
        "nsfit_not_started",
        "failed_can_reattempt",
        "voluntary_fit_in_progress",
        "mandatory_fit_in_progress",
        "mandatory_fit_failed_attempt",
    ):
        assert tier_highlighted("tier_3", state, True, "fit")
    for state in ("met_award", "met_voluntary_fit", "met_mandatory_fit", "waived"):
        assert not tier_highlighted("tier_3", state, None, None)


def test_no_band_never_highlights():
    assert not tier_highlighted(None, "ippt_not_attempted", False, "na")


# ============================================================================
# Matching normalisation (§3.2)
# ============================================================================


def test_normalize_name_uppercases_and_collapses_whitespace():
    assert normalize_name("  TAN   Kai Keong,  DERRICK ") == "TAN KAI KEONG, DERRICK"
    assert normalize_name("VINOD S/O RAJANDRAN MUTHU") == "VINOD S/O RAJANDRAN MUTHU"


# ============================================================================
# The OneNS "All Tabs" workbook (docs/IPPT_MONITORING.md §2.1, 2026-11-03)
# ============================================================================

ALL_TABS_DATE = date(2026, 11, 3)


def _workbook_bytes(sheets: dict[str, list[list]]) -> bytes:
    """An in-memory workbook with one sheet per given name, in order."""
    buffer = io.BytesIO()
    workbook = Workbook()
    first = True
    for name, rows in sheets.items():
        sheet = workbook.active if first else workbook.create_sheet()
        first = False
        assert sheet is not None
        sheet.title = name
        for row in rows:
            sheet.append(row)
    workbook.save(buffer)
    return buffer.getvalue()


def _all_tabs_sheets(rows: dict[str, list[list]]) -> dict[str, list[list]]:
    """The six real tab names, header rows included; per-tab data rows
    given as cell lists, empty list = empty-but-present population."""
    families = {
        "(IPPT) Not Started": (
            "Rank",
            "Name",
            "Unit",
            "Sub-unit",
            "Window close",
            "Booked Status",
            "FFI",
            "Reminder sent",
            "Last sent",
        ),
        "(IPPT) Failed or FIT": (
            "Rank",
            "Name",
            "Unit",
            "Sub-unit",
            "Window close",
            "IPPT status",
            "FIT sessions completed",
            "Reminder sent",
            "Last sent",
        ),
        "(IPPT) Passed or Completed": (
            "Rank",
            "Name",
            "Unit",
            "Sub-unit",
            "IPPT status",
            "FIT sessions completed",
        ),
        "(NSFIT) Not Started": (
            "Rank",
            "Name",
            "Unit",
            "Sub-unit",
            "Window close",
            "Booked Status",
            "FFI",
            "Reminder sent",
            "Last sent",
        ),
        "(NSFIT) In Progress": (
            "Rank",
            "Name",
            "Unit",
            "Sub-unit",
            "Window close",
            "FIT sessions completed",
            "IPPT status",
            "Reminder sent",
            "Last sent",
        ),
        "(NSFIT) Completed": (
            "Rank",
            "Name",
            "Unit",
            "Sub-unit",
            "IPPT status",
            "FIT sessions completed",
        ),
    }
    return {
        name: [list(header), *rows.get(name, [])] for name, header in families.items()
    }


def test_parse_all_tabs_filename():
    assert parse_all_tabs_filename("All_Tabs_Export_20261103.xlsx") == ALL_TABS_DATE
    assert parse_all_tabs_filename("all_tabs_export_20261103.XLSX") == ALL_TABS_DATE
    with pytest.raises(FilenameError):
        parse_all_tabs_filename("All_Tabs_Export_20261103.csv")  # flattened form
    with pytest.raises(FilenameError):
        parse_all_tabs_filename("All_Tabs_Export.xlsx")


def test_is_all_tabs_filename_recognizes_both_extensions():
    assert is_all_tabs_filename("All_Tabs_Export_20261103.xlsx")
    assert is_all_tabs_filename("All_Tabs_Export_20261103.csv")  # to reject it
    assert not is_all_tabs_filename("IPPT_COMPLETED_20261103.csv")
    assert not is_all_tabs_filename("random.xlsx")


def test_all_tabs_workbook_parses_to_six_kinds():
    content = _workbook_bytes(
        _all_tabs_sheets(
            {
                "(IPPT) Not Started": [
                    [
                        "ME1   ",
                        "KO ENG LIANG",
                        "DK314",
                        "FORWARD MAINT COY (LIGHT)",
                        20,
                        "Yes",
                        "NA",
                        0,
                        "-",
                    ],
                    [
                        "MAJ   ",
                        "WANG YI LIANG",
                        "DK314",
                        "NON-ESTAB",
                        24,
                        "No",
                        "Fit",
                        0,
                        "-",
                    ],
                ],
                "(IPPT) Failed or FIT": [
                    [
                        "CPL   ",
                        "NO ATTEMPT PERSON",
                        "DK314",
                        "BN HQ",
                        4,
                        "-",
                        0,
                        0,
                        "-",
                    ],
                    [
                        "LCP   ",
                        "FIT PERSON",
                        "DK314",
                        "MEDICAL COY",
                        18,
                        "-",
                        3,
                        0,
                        "-",
                    ],
                ],
                "(IPPT) Passed or Completed": [
                    ["SSG   ", "PASS PERSON", "DK314", "SECURITY COY", "Pass", 0],
                    ["CPL   ", "WAIVED PERSON", "DK314", "BN HQ", "-", "-"],
                ],
            }
        )
    )
    parsed = parse_all_tabs_workbook("All_Tabs_Export_20261103.xlsx", content)
    assert [p.file_kind for p in parsed] == [
        "ippt_not_attempted",
        "ippt_failed",
        "ippt_completed",
        "nsfit_not_started",
        "nsfit_in_progress",
        "nsfit_completed",
    ]
    assert all(p.report_date == ALL_TABS_DATE for p in parsed)
    assert all(p.filename == "All_Tabs_Export_20261103.xlsx" for p in parsed)
    assert all(p.quarantined == [] for p in parsed)
    by_kind = {p.file_kind: p for p in parsed}
    assert [r.state for r in by_kind["ippt_not_attempted"].rows] == [
        "ippt_not_attempted",
        "ippt_not_attempted",
    ]
    first = by_kind["ippt_not_attempted"].rows[0]
    assert first.rank == "ME1"  # padding stripped
    assert first.booked is True
    assert first.ffi == "na"
    assert first.last_sent is None
    assert first.reminder_sent == "0"
    # The '-' + 0-session domain call on the failed/FIT tab.
    assert [r.state for r in by_kind["ippt_failed"].rows] == [
        "ippt_not_attempted",
        "voluntary_fit_in_progress",
    ]
    assert [r.state for r in by_kind["ippt_completed"].rows] == [
        "met_award",
        "waived",
    ]
    # Empty populations are valid: header-only tabs, zero rows.
    assert by_kind["nsfit_not_started"].rows == []
    assert by_kind["nsfit_in_progress"].rows == []
    assert by_kind["nsfit_completed"].rows == []


def test_all_tabs_workbook_ignores_extra_sheets_and_name_drift():
    sheets = _all_tabs_sheets({})
    sheets["Notes"] = [["scratch", "rows"]]
    # Case/whitespace drift on one tab name still matches.
    sheets["(ippt)  Not   Started"] = sheets.pop("(IPPT) Not Started")
    parsed = parse_all_tabs_workbook(
        "All_Tabs_Export_20261103.xlsx", _workbook_bytes(sheets)
    )
    assert len(parsed) == 6
    assert {p.file_kind for p in parsed} == {
        "ippt_completed",
        "ippt_failed",
        "ippt_not_attempted",
        "nsfit_completed",
        "nsfit_in_progress",
        "nsfit_not_started",
    }


def test_all_tabs_workbook_rejects_missing_tab():
    sheets = _all_tabs_sheets({})
    del sheets["(NSFIT) Completed"]
    with pytest.raises(ReportFileError, match="missing required tab"):
        parse_all_tabs_workbook(
            "All_Tabs_Export_20261103.xlsx", _workbook_bytes(sheets)
        )


def test_all_tabs_workbook_rejects_headerless_tab():
    sheets = _all_tabs_sheets({})
    sheets["(NSFIT) Completed"] = []  # empty sheet, not even a header
    with pytest.raises(ReportFileError, match="no header row"):
        parse_all_tabs_workbook(
            "All_Tabs_Export_20261103.xlsx", _workbook_bytes(sheets)
        )


def test_all_tabs_workbook_quarantines_bad_row_without_rejecting():
    sheets = _all_tabs_sheets(
        {
            "(IPPT) Not Started": [
                ["PTE   ", "GOOD ROW", "DK314", "BN HQ", 30, "No", "NA", 0, "-"],
                ["PTE   ", "BAD ROW", "DK314", "BN HQ", "soon", "No", "NA", 0, "-"],
            ]
        }
    )
    parsed = parse_all_tabs_workbook(
        "All_Tabs_Export_20261103.xlsx", _workbook_bytes(sheets)
    )
    by_kind = {p.file_kind: p for p in parsed}
    assert len(by_kind["ippt_not_attempted"].rows) == 1
    assert len(by_kind["ippt_not_attempted"].quarantined) == 1
    assert "not an integer" in by_kind["ippt_not_attempted"].quarantined[0].reason


def test_all_tabs_workbook_rejects_unreadable_bytes():
    with pytest.raises(ReportFileError, match="unreadable"):
        parse_all_tabs_workbook(
            "All_Tabs_Export_20261103.xlsx", b"definitely not a zip"
        )


ALL_TABS_FIXTURE = IPPT_FIXTURES / "All_Tabs_Export_20261103.xlsx"


def test_all_tabs_fixture_parses_to_observed_counts():
    """The real 2026-11-03 export (skipped when the gitignored fixture
    is absent): 213 rows, zero quarantined, every derived state as
    scanned from the workbook."""
    if not ALL_TABS_FIXTURE.exists():
        pytest.skip("All Tabs fixture not present — /fixtures is gitignored")
    parsed = parse_all_tabs_workbook(
        ALL_TABS_FIXTURE.name, ALL_TABS_FIXTURE.read_bytes()
    )
    by_kind = {p.file_kind: p for p in parsed}
    expected_rows = {
        "ippt_not_attempted": 76,
        "ippt_failed": 39,
        "ippt_completed": 98,
        "nsfit_not_started": 0,
        "nsfit_in_progress": 0,
        "nsfit_completed": 0,
    }
    expected_states = {
        "ippt_not_attempted": {"ippt_not_attempted": 76},
        "ippt_failed": {
            "failed_can_reattempt": 30,
            "voluntary_fit_in_progress": 9,
        },
        "ippt_completed": {
            "met_award": 87,
            "met_voluntary_fit": 8,
            "waived": 3,
        },
    }
    for kind, count in expected_rows.items():
        assert by_kind[kind].report_date == ALL_TABS_DATE
        assert len(by_kind[kind].rows) == count, kind
        assert by_kind[kind].quarantined == [], kind
        if count:
            states: dict[str, int] = {}
            for row in by_kind[kind].rows:
                states[row.state] = states.get(row.state, 0) + 1
            assert states == expected_states[kind], kind


# ============================================================================
# Canonical fixture acceptance (§2.4) — skipped when /fixtures is absent
# (gitignored); the repo copy carries the 2026-09-11 sample snapshot.
# ============================================================================


EXPECTED_FIXTURE_STATES = {
    "IPPT_COMPLETED_20260911.csv": {
        "rows": 49,
        "states": {
            "met_award": 38,
            "met_voluntary_fit": 6,
            "waived": 5,
        },
    },
    "IPPT_FAILED_20260911.csv": {
        "rows": 31,
        "states": {
            "failed_can_reattempt": 13,
            "voluntary_fit_in_progress": 18,
        },
    },
    "IPPT_NOT_ATTEMPTED_20260911.csv": {
        "rows": 132,
        "states": {"ippt_not_attempted": 132},
    },
    "NSFIT_COMPLETED_20260911.csv": {
        "rows": 5,
        "states": {"met_mandatory_fit": 5},
    },
    "NSFIT_IN_PROGRESS_20260911.csv": {
        "rows": 9,
        "states": {
            "mandatory_fit_in_progress": 8,
            "mandatory_fit_failed_attempt": 1,
        },
    },
    "NSFIT_NOT_STARTED_20260911.csv": {
        "rows": 58,
        "states": {"nsfit_not_started": 58},
    },
}


@pytest.mark.parametrize("filename", sorted(EXPECTED_FIXTURE_STATES))
def test_canonical_fixture_parses_to_spec_counts(filename):
    path = IPPT_FIXTURES / filename
    if not path.exists():
        pytest.skip("canonical IPPT fixture not present — /fixtures is gitignored")
    parsed = parse_report_file(path.name, path.read_text())
    expected = EXPECTED_FIXTURE_STATES[filename]

    assert parsed.report_date == date(2026, 9, 11)
    assert len(parsed.rows) == expected["rows"]
    assert parsed.quarantined == []
    counts: dict[str, int] = {}
    for row in parsed.rows:
        counts[row.state] = counts.get(row.state, 0) + 1
    assert counts == expected["states"]


@pytest.mark.parametrize(
    "csv_path",
    sorted(IPPT_FIXTURES.glob("*.csv")),
    ids=lambda path: path.name,
)
def test_canonical_xlsx_fixture_parses_identically_to_csv(csv_path):
    """Real .xlsx exports of the same reports must parse to exactly the
    rows their .csv twins produce (skipped silently per file when the
    gitignored fixture or its .xlsx twin is absent)."""
    xlsx_path = csv_path.with_suffix(".xlsx")
    if not xlsx_path.exists():
        pytest.skip("no .xlsx twin for this fixture")

    def tuples(parsed):
        return [
            (
                row.line_number,
                row.rank,
                row.full_name,
                row.state,
                row.window_close_days,
                row.ippt_status,
                row.fit_sessions,
                row.booked,
                row.ffi,
            )
            for row in parsed.rows
        ]

    csv_parsed = parse_report_file(csv_path.name, csv_path.read_text())
    xlsx_parsed = parse_report_file(xlsx_path.name, xlsx_path.read_bytes())
    assert tuples(xlsx_parsed) == tuples(csv_parsed)
    assert xlsx_parsed.quarantined == csv_parsed.quarantined
