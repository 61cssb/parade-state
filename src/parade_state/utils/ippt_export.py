"""XLSX export of the IPPT monitoring dashboard (pure, no DB).

One workbook, five tabs in the dashboard's section order: the three
escalation tiers (3/6/9 months before window close, most urgent first),
the on-track roster, and the fulfilled set. The export is always the
full dataset — the dashboard's filter/sort bar is a viewing aid and
never reaches the download — with rows in the base most-urgent-first
order.

Cell contents mirror what the page renders (decided 2026-10-19): state
labels keep their FIT-count and award parentheticals, Booked is the
Yes/No/``-`` tri-state, FFI reads Pending/Fit/NA/``-``, and ``-`` marks
a blank exactly as on screen — except "Window ends", which is written
as a real Excel date (displayed ``yyyy-mm-dd``) so the workbook sorts
and filters natively.
"""

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

#: Tab titles in render order, matching the dashboard's section names.
TIER_SHEETS: tuple[tuple[str, str], ...] = (
    ("tier_3", "3 months"),
    ("tier_6", "6 months"),
    ("tier_9", "9 months"),
)

TIER_HEADERS: tuple[str, ...] = (
    "Rank",
    "Name",
    "Sub-unit",
    "State",
    "Booked",
    "FFI",
    "Window ends",
    "Days left",
)
ONTRACK_HEADERS: tuple[str, ...] = ("Rank", "Name", "Sub-unit", "State", "Window ends")
FULFILLED_HEADERS: tuple[str, ...] = ("Rank", "Name", "Sub-unit", "How")

_DATE_FORMAT = "yyyy-mm-dd"


def build_dashboard_workbook(dashboard: dict) -> bytes:
    """Render a :func:`services.ippt.dashboard_data` payload into a
    five-tab ``.xlsx`` workbook, returned as bytes.

    The tier tabs come from ``dashboard["tiers"]``, the fulfilled tab
    from ``dashboard["fulfilled"]``, and the on-track tab is derived
    from the full ``dashboard["servicemen"]`` list the same way the
    page derives its on-track section: not highlighted, not fulfilled.
    """
    servicemen = dashboard["servicemen"]
    ontrack = [e for e in servicemen if not e["highlighted"] and not e["fulfilled"]]

    workbook = Workbook()
    workbook.remove(workbook.active)
    for tier_key, title in TIER_SHEETS:
        _add_sheet(
            workbook,
            title,
            TIER_HEADERS,
            (_tier_row(e) for e in dashboard["tiers"][tier_key]),
        )
    _add_sheet(
        workbook, "On track", ONTRACK_HEADERS, (_ontrack_row(e) for e in ontrack)
    )
    _add_sheet(
        workbook,
        "Fulfilled",
        FULFILLED_HEADERS,
        (_fulfilled_row(e) for e in dashboard["fulfilled"]),
    )

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _add_sheet(workbook: Workbook, title: str, headers: tuple[str, ...], rows) -> None:
    sheet = workbook.create_sheet(title=title)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
        # A date cell sits in the last column of the tier/on-track
        # layouts; stamp the display format the header promises.
        if len(row) == len(headers) and isinstance(row[-1], date):
            sheet.cell(
                row=sheet.max_row, column=len(headers)
            ).number_format = _DATE_FORMAT
    sheet.freeze_panes = "A2"
    _autosize(sheet)


def _autosize(sheet) -> None:
    """Column widths from the longest cell (header included), capped so
    a pathological name cannot stretch a sheet."""
    for cells in sheet.columns:
        width = max(
            len(str(cell.value)) if cell.value is not None else 0 for cell in cells
        )
        sheet.column_dimensions[get_column_letter(cells[0].column)].width = min(
            max(width + 2, 8), 40
        )


def _tier_row(entry: dict) -> list:
    state = entry["state_label"]
    if entry["fit_sessions"]:
        state = f"{state} ({entry['fit_sessions']} FIT)"
    days = entry["days_to_close"]
    return [
        entry["rank"],
        entry["full_name"],
        entry["sub_unit"],
        state,
        _booked_text(entry["booked"]),
        _ffi_text(entry["ffi"]),
        _date_or_dash(entry["window_end"]),
        days if days is not None else "-",
    ]


def _ontrack_row(entry: dict) -> list:
    return [
        entry["rank"],
        entry["full_name"],
        entry["sub_unit"],
        entry["state_label"],
        _date_or_dash(entry["window_end"]),
    ]


def _fulfilled_row(entry: dict) -> list:
    how = entry["state_label"]
    if entry["ippt_status"]:
        how = f"{how} ({entry['ippt_status']})"
    return [entry["rank"], entry["full_name"], entry["sub_unit"], how]


def _booked_text(booked: bool | None) -> str:
    return "-" if booked is None else ("Yes" if booked else "No")


def _ffi_text(ffi: str | None) -> str:
    if not ffi:
        return "-"
    if ffi == "pending":
        return "Pending"
    return ffi.capitalize()


def _date_or_dash(iso: str | None) -> date | str:
    return date.fromisoformat(iso) if iso else "-"
