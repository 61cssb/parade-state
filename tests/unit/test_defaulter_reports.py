"""Parser tests for the quarterly defaulter release
(utils/defaulter_reports.py).

The fixtures mirror the live export's shape: stray preamble headers
from other report formats (different column sets, no data rows),
blank separator rows, a Pending section, then an Archived section whose
``number of times defaulted`` column is ``DEFAULTER COUNT`` instead of
``SAF264 ISSUED``.
"""

import io

import pytest
from openpyxl import Workbook

from parade_state.utils.defaulter_reports import (
    QuarterError,
    parse_defaulter_file,
    validate_quarter,
)
from parade_state.utils.ippt_reports import ReportFileError

PENDING_HEADER = (
    "TYPE,UNIT,PERS,RANK,NAME,OFFENCE TYPE,OFFENCE WINDOW,DEFAULT COUNT,"
    "SAF264 ISSUED,SCHEDULED SUMMARY TRIAL,MAIL MERGE DATE,DUE IN (DAYS),"
    "AMENDMENT STATUS,,,"
)
ARCHIVED_HEADER = (
    "TYPE,UNIT,PERS,RANK,NAME,OFFENCE TYPE,OFFENCE WINDOW,DEFAULT COUNT,"
    "DEFAULTER COUNT,DISCIPLINARY ACTION,CHARGE STATUS,SCHEDULED SUMMARY TRIAL,"
    "VERDICT,JUSTIFICATION,OFFENCE AUTH ID,CHARGE AUTH ID,,,"
)
PREAMBLE_HEADER = (
    "TYPE,UNIT,PERS,RANK,NAME,OFFENCE TYPE,OFFENCE WINDOW,DEFAULT COUNT,"
    "PAST OFFENCES,DISCIPLINARY ACTION,COMPOSITION AMOUNT,DUE IN (DAYS),,,"
)

PENDING_ROW = (
    "Pending ST,61 CSSB,11104490,LCP ,ARAVIND NAIDU S/O GOVINDARAJOO,"
    'NS FIT Defaulter,25/08/2025 - 24/08/2026,6,3,"20/10/2026, 10:45",'
    "28/09/2026,294,N.A."
)
ARCHIVED_ROW = (
    "Archived,61 CSSB,11168878,CPL ,ALSON WONG YAN HAN,NS FIT Defaulter,"
    '03/07/2025 - 02/07/2026,3,1,Issued SWL (Reset),SWL issued,,"10/09/2026, 09:45",'
    "N.A.,CDMB,CDMB"
)


def _sample_csv() -> bytes:
    """The live file's shape: preamble headers, blank runs, the two
    data-carrying sections."""
    return "\n".join(
        [
            PREAMBLE_HEADER,
            "",
            PREAMBLE_HEADER,
            "",
            PREAMBLE_HEADER,
            "",
            PENDING_HEADER,
            PENDING_ROW,
            "Pending ST,61 CSSB,11155051,CPL ,MUHAMMAD NUR HAZRI BIN HAMZAH,"
            "NS FIT Defaulter,07/08/2025 - 06/08/2026,5,3,"
            '"20/10/2026, 11:15",28/09/2026,276,N.A.',
            "",
            ARCHIVED_HEADER,
            ARCHIVED_ROW,
            "",
        ]
    ).encode()


class TestMultiSectionCsv:
    def test_full_sample(self):
        parsed = parse_defaulter_file("release.csv", _sample_csv())
        assert len(parsed.rows) == 3
        assert len(parsed.quarantined) == 0

        first, second, third = parsed.rows
        assert (first.row_type, first.line_number) == ("pending", 8)
        assert first.rank == "LCP"  # trailing space in the cell is stripped
        assert first.pers_no == "11104490"
        assert first.default_count == 6
        assert first.defaulted_count == 3  # SAF264 ISSUED
        assert first.offence_window == "25/08/2025 - 24/08/2026"
        assert "GOVINDARAJOO" in first.full_name

        assert (third.row_type, third.line_number) == ("archived", 12)
        assert third.defaulted_count == 1  # DEFAULTER COUNT (archived header)

    def test_name_norm_property(self):
        parsed = parse_defaulter_file("release.csv", _sample_csv())
        assert parsed.rows[0].full_name_norm == parsed.rows[0].full_name.upper()

    def test_type_column_absent_means_pending(self):
        header = PENDING_HEADER.replace("TYPE,", "", 1)
        row = PENDING_ROW.split(",", 1)[1]
        content = "\n".join([header, row]).encode()
        parsed = parse_defaulter_file("release.csv", content)
        assert len(parsed.rows) == 1
        assert parsed.rows[0].row_type == "pending"


class TestSectionRules:
    def test_preamble_headers_without_data_are_ignored(self):
        parsed = parse_defaulter_file("release.csv", _sample_csv())
        # No quarantine, no rows from the three preamble headers.
        assert all(row.line_number >= 8 for row in parsed.rows)

    def test_data_rows_missing_required_column_reject_file(self):
        header = PENDING_HEADER.replace("SAF264 ISSUED,", "", 1)
        row = PENDING_ROW  # still has all its cells — the header lacks the column
        content = "\n".join([header, row]).encode()
        with pytest.raises(ReportFileError, match="SAF264 ISSUED or DEFAULTER COUNT"):
            parse_defaulter_file("release.csv", content)

    def test_row_before_any_header_quarantines(self):
        # Rows before the first header reject the file outright; inside a
        # section, a row the TYPE column can't classify quarantines.
        content = "\n".join(
            ["stray,line,with,no,section", PENDING_HEADER, PENDING_ROW]
        ).encode()
        with pytest.raises(ReportFileError, match="first row is not a section header"):
            parse_defaulter_file("release.csv", content)


class TestRowValidation:
    def test_bad_type_value_quarantines(self):
        bad = PENDING_ROW.replace("Pending ST", "Whatever", 1)
        content = "\n".join([PENDING_HEADER, PENDING_ROW, bad]).encode()
        parsed = parse_defaulter_file("release.csv", content)
        assert len(parsed.rows) == 1
        assert len(parsed.quarantined) == 1
        assert "TYPE must be Pending or Archived" in parsed.quarantined[0].reason

    def test_empty_name_quarantines(self):
        bad = PENDING_ROW.replace("ARAVIND NAIDU S/O GOVINDARAJOO", "", 1)
        content = "\n".join([PENDING_HEADER, bad]).encode()
        parsed = parse_defaulter_file("release.csv", content)
        assert parsed.rows == []
        assert "NAME is required" in parsed.quarantined[0].reason

    def test_non_integer_count_quarantines(self):
        bad = PENDING_ROW.replace(",6,3,", ",six,3,", 1)
        content = "\n".join([PENDING_HEADER, bad]).encode()
        parsed = parse_defaulter_file("release.csv", content)
        assert parsed.rows == []
        assert "DEFAULT COUNT must be a whole number" in parsed.quarantined[0].reason


class TestFileLevel:
    def test_empty_file_rejects(self):
        with pytest.raises(ReportFileError, match="file is empty"):
            parse_defaulter_file("release.csv", b"\n\n\n")

    def test_first_row_not_a_header_rejects(self):
        with pytest.raises(ReportFileError, match="first row is not a section header"):
            parse_defaulter_file("release.csv", b"not,a,header\n")

    def test_header_only_file_rejects(self):
        content = "\n".join([PREAMBLE_HEADER, ""]).encode()
        with pytest.raises(ReportFileError, match="no defaulter rows found"):
            parse_defaulter_file("release.csv", content)

    def test_xlsx_first_tab(self):
        buffer = io.BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.append(PENDING_HEADER.split(","))
        sheet.append(
            [
                "Pending ST",
                "61 CSSB",
                11104490,
                "LCP",
                "ARAVIND NAIDU S/O GOVINDARAJOO",
                "NS FIT Defaulter",
                "25/08/2025 - 24/08/2026",
                6,
                3,
                "20/10/2026, 10:45",
                "28/09/2026",
                294,
                "N.A.",
            ]
        )
        workbook.save(buffer)
        parsed = parse_defaulter_file("release.xlsx", buffer.getvalue())
        assert len(parsed.rows) == 1
        row = parsed.rows[0]
        assert row.pers_no == "11104490"  # Excel's 11104490.0 stringified
        assert row.defaulted_count == 3
        assert row.row_type == "pending"


class TestQuarter:
    def test_valid_quarters(self):
        assert validate_quarter(2026, 1) == (2026, 1)
        assert validate_quarter(2026, 4) == (2026, 4)

    @pytest.mark.parametrize("quarter", [0, 5, -1])
    def test_invalid_quarter(self, quarter):
        with pytest.raises(QuarterError, match="Quarter must be 1–4"):
            validate_quarter(2026, quarter)

    @pytest.mark.parametrize("year", [1999, 2101])
    def test_year_bounds(self, year):
        with pytest.raises(QuarterError, match="Year must be"):
            validate_quarter(year, 1)
