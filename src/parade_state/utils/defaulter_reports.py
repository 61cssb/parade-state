"""Parser for the unit's quarterly defaulter release (docs/
IPPT_MONITORING.md §7).

The export is one file (CSV, or an XLSX whose **first tab** carries the
data) containing several stacked sections, each introduced by its own
header row (recognised by its ``NAME``/``OFFENCE TYPE`` marker columns;
usually the first cell is ``TYPE``); blank rows separate them.
The live file has stray preamble headers from other report formats
(different column sets, no data rows beneath) — those are ignored, not
errors. Sections that DO carry data rows but lack a required column
reject the whole submission: none of their rows can be interpreted
reliably, so guessing is worse than bouncing the upload (§4's
file-level-problems-reject stance).

Required columns (matched case-insensitively on whitespace-collapsed
names): ``UNIT``, ``RANK``, ``NAME``, ``OFFENCE TYPE``,
``OFFENCE WINDOW``, ``DEFAULT COUNT`` and the number of times
defaulted — ``SAF264 ISSUED`` in Pending sections, ``DEFAULTER COUNT``
in Archived ones (same slot, section-dependent header). ``TYPE`` and
``PERS`` are optional: a file without ``TYPE`` is all Pending, a row
without ``PERS`` is matched by name alone. Everything else in the file
(SCHEDULED SUMMARY TRIAL, MAIL MERGE DATE, DUE IN (DAYS), AMENDMENT
STATUS, DISCIPLINARY ACTION, VERDICT, ...) is ignored.

The quarter is NOT in the file — the user picks it at upload and the
service stores it verbatim; nothing here derives or validates it
against the offence windows.
"""

import csv
import datetime
import io

from openpyxl import load_workbook
from pydantic import BaseModel

from .ippt_reports import ReportFileError, normalize_name

#: Required columns of any data-carrying section. The defaulted-count
#: slot accepts either header (SAF264 ISSUED = Pending, DEFAULTER
#: COUNT = Archived); at least one must be present.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "UNIT",
    "RANK",
    "NAME",
    "OFFENCE TYPE",
    "OFFENCE WINDOW",
    "DEFAULT COUNT",
)
DEFAULTED_COUNT_COLUMNS: tuple[str, ...] = ("SAF264 ISSUED", "DEFAULTER COUNT")

#: Optional columns read when present.
TYPE_COLUMN = "TYPE"
PERS_COLUMN = "PERS"

_MIN_YEAR = 2000
_MAX_YEAR = 2100


class QuarterError(ValueError):
    """The upload's quarter label is not a calendar quarter."""


def validate_quarter(year: int, quarter: int) -> tuple[int, int]:
    """Validate the user-chosen quarter: calendar quarter 1–4 (Q1 =
    Jan 1 – Mar 31, the workyear's Apr–Mar span notwithstanding) and a
    sane year. Returns ``(year, quarter)`` unchanged.

    Raises:
        QuarterError: quarter not in 1–4, or year outside the accepted
            range.
    """
    if quarter not in (1, 2, 3, 4):
        raise QuarterError(f"Quarter must be 1–4, got {quarter!r}")
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        raise QuarterError(f"Year must be {_MIN_YEAR}–{_MAX_YEAR}, got {year!r}")
    return year, quarter


class DefaulterRowData(BaseModel):
    """One parsed defaulter row, ready for the service layer."""

    line_number: int
    row_type: str  # 'pending' | 'archived'
    unit: str
    pers_no: str | None
    rank: str
    full_name: str
    offence_type: str
    offence_window: str
    default_count: int
    defaulted_count: int

    @property
    def full_name_norm(self) -> str:
        return normalize_name(self.full_name)


class QuarantinedEntry(BaseModel):
    """A rejected row kept for review: raw line + why."""

    line_number: int
    raw_row: str
    reason: str


class ParsedDefaulterFile(BaseModel):
    filename: str
    rows: list[DefaulterRowData] = []
    quarantined: list[QuarantinedEntry] = []


def parse_defaulter_file(filename: str, content: bytes | str) -> ParsedDefaulterFile:
    """Parse a quarterly defaulter release (CSV or first-tab XLSX).

    Filename is free-form (the export name carries no quarter) and is
    kept only for provenance and error messages. Header/section-level
    problems raise :class:`ReportFileError` (whole submission rejected);
    per-row value problems land in ``quarantined`` and the rest of the
    file ingests (§4).

    Raises:
        ReportFileError: unreadable or empty file, a data-carrying
            section missing required columns, or no data at all.
    """
    if filename.lower().endswith(".xlsx"):
        rows = _xlsx_first_tab_rows(filename, content)
    else:
        rows = _csv_rows(filename, content)
    return _parse_matrix(filename, rows)


def _csv_rows(filename: str, content: bytes | str) -> list[list[str]]:
    """The raw CSV row matrix, UTF-8/ASCII only."""
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReportFileError(f"{filename}: not UTF-8/ASCII text: {exc}") from exc
    return list(csv.reader(io.StringIO(content)))


def _xlsx_first_tab_rows(filename: str, content: bytes | str) -> list[list[str]]:
    """The first worksheet as a raw row matrix, every cell stringified
    the way its CSV export would read."""
    try:
        workbook = load_workbook(
            io.BytesIO(content),  # type: ignore[arg-type]
            read_only=True,
            data_only=True,
        )
    except Exception as exc:  # noqa: BLE001 — any zip/XML defect is "unusable"
        raise ReportFileError(f"{filename}: unreadable .xlsx workbook: {exc}") from exc
    try:
        sheet = workbook.active
        if sheet is None:
            raise ReportFileError(f"{filename}: workbook has no sheets")
        return [
            [_cell_text(cell) for cell in row]
            for row in sheet.iter_rows(values_only=True)
        ]
    finally:
        workbook.close()


def _cell_text(value) -> str:
    """Stringify one worksheet cell the way the CSV reader would have."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # Excel stores 6 as 6.0
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def _norm_header(name: str) -> str:
    """Header names match case-insensitively on collapsed whitespace."""
    return " ".join(name.split()).casefold()


def _is_header_row(row: list[str]) -> bool:
    """A section header, recognised by its marker columns: ``NAME`` and
    ``OFFENCE TYPE`` as cells. First-cell ``TYPE`` is the usual tell but
    is not required (the export may omit the TYPE column entirely), and
    marker columns survive column reordering. Data rows never carry
    ``OFFENCE TYPE`` as a literal cell value."""
    cells = {_norm_header(value) for value in row if value and value.strip()}
    return "name" in cells and "offence type" in cells


def _parse_matrix(filename: str, rows: list[list[str]]) -> ParsedDefaulterFile:
    """Section-split + per-row parsing of a raw row matrix.

    A header row (marker columns, see :func:`_is_header_row`) opens a
    section; the rows after it belong to it until the next header or
    blank run. A section header with no data rows before the next header
    (or EOF) is stray preamble — ignored. The first row must be a header
    (the export always leads with one; anything else means the wrong
    file was picked), and after it every non-blank row is consumed by a
    section, so no attribution gaps can arise.
    """
    if not any((value or "").strip() for row in rows for value in row):
        raise ReportFileError(f"{filename}: file is empty")
    if not _is_header_row(rows[0]):
        raise ReportFileError(
            f"{filename}: first row is not a section header "
            "(expected the export's column headers)"
        )

    parsed = ParsedDefaulterFile(filename=filename)
    seen_data = False

    index = 0
    while index < len(rows):
        if not any((value or "").strip() for value in rows[index]):
            index += 1
            continue
        # rows[index] is always a header here: the first-row check above,
        # then every section consumes through its next header (or EOF).
        header_index, data_indexes, index = _collect_section(rows, index)
        new_rows, new_quarantined = _parse_section(
            filename, rows[header_index], rows, data_indexes
        )
        parsed.rows.extend(new_rows)
        parsed.quarantined.extend(new_quarantined)
        if new_rows or new_quarantined:
            seen_data = True

    if not seen_data:
        raise ReportFileError(
            f"{filename}: no defaulter rows found — every section header "
            "is followed by empty data (is this the right export?)"
        )
    return parsed


def _collect_section(
    rows: list[list[str]], header_index: int
) -> tuple[int, list[int], int]:
    """The header's index (passed through), the line indexes of the
    section's data rows, and the index just past the section (its next
    header row or EOF)."""
    data_indexes: list[int] = []
    index = header_index + 1
    while index < len(rows):
        row = rows[index]
        if not any((value or "").strip() for value in row):
            index += 1
            continue
        if _is_header_row(row):
            break
        data_indexes.append(index)
        index += 1
    return header_index, data_indexes, index


def _parse_section(
    filename: str,
    header: list[str],
    rows: list[list[str]],
    data_indexes: list[int],
) -> tuple[list[DefaulterRowData], list[QuarantinedEntry]]:
    """One section: resolve its columns, parse each data row.

    A section with data rows but missing required columns rejects the
    whole file (the caller turns ReportFileError into a 422); a header
    with no data rows returns empty (stray preamble)."""
    if not data_indexes:
        return [], []

    names = {_norm_header(name): position for position, name in enumerate(header)}
    missing = [name for name in REQUIRED_COLUMNS if _norm_header(name) not in names]
    defaulted_column = next(
        (name for name in DEFAULTED_COUNT_COLUMNS if _norm_header(name) in names),
        None,
    )
    if defaulted_column is None:
        missing.append(" or ".join(DEFAULTED_COUNT_COLUMNS))
    if missing:
        raise ReportFileError(
            f"{filename}: a section with {len(data_indexes)} data row(s) is "
            f"missing required column(s): {', '.join(missing)}"
        )

    def cell(row: list[str], column: str) -> str | None:
        position = names[_norm_header(column)]
        value = row[position] if position < len(row) else None
        return value.strip() if value is not None else None

    has_type = _norm_header(TYPE_COLUMN) in names
    has_pers = _norm_header(PERS_COLUMN) in names

    parsed: list[DefaulterRowData] = []
    quarantined: list[QuarantinedEntry] = []
    for line_number in data_indexes:
        row = rows[line_number]
        raw = ",".join(row)
        try:
            parsed.append(
                DefaulterRowData(
                    line_number=line_number + 1,
                    row_type=_row_type(cell, row, has_type),
                    unit=cell(row, "UNIT") or "",
                    pers_no=cell(row, PERS_COLUMN) if has_pers else None,
                    rank=cell(row, "RANK") or "",
                    full_name=_required(cell, row, "NAME"),
                    offence_type=_required(cell, row, "OFFENCE TYPE"),
                    offence_window=_required(cell, row, "OFFENCE WINDOW"),
                    default_count=_int_cell(cell, row, "DEFAULT COUNT"),
                    defaulted_count=_int_cell(cell, row, defaulted_column),
                )
            )
        except ValueError as exc:
            quarantined.append(
                QuarantinedEntry(
                    line_number=line_number + 1, raw_row=raw, reason=str(exc)
                )
            )
    return parsed, quarantined


def _row_type(cell, row: list[str], has_type: bool) -> str:
    """The row's TYPE: ``Archived`` → archived; ``Pending …``/absent
    column/blank cell → pending; anything else raises (row quarantined).
    A blank cell under a present TYPE column carries no contrary signal,
    so it reads as pending too."""
    if not has_type:
        return "pending"
    value = cell(row, TYPE_COLUMN)
    if not value:
        return "pending"
    folded = value.casefold()
    if folded == "archived":
        return "archived"
    if folded.startswith("pending"):
        return "pending"
    raise ValueError(f"TYPE must be Pending or Archived, got {value!r}")


def _required(cell, row: list[str], column: str) -> str:
    value = cell(row, column)
    if not value:
        raise ValueError(f"{column} is required but the cell is empty")
    return value


def _int_cell(cell, row: list[str], column: str) -> int:
    value = cell(row, column)
    if not value:
        raise ValueError(f"{column} is required but the cell is empty")
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{column} must be a whole number, got {value!r}") from None
