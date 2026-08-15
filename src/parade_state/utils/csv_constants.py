"""Canonical CSV column mapping for the 61 CSSB WY2627 ICT fixture.

Single source of truth for the column layout used by both the standalone
demo ingester (``experiments/csv_to_nr/ingest.py``) and the app-side CSV
process endpoint (``parade_state.api.csv_upload``). Lifting these into a
shared module prevents the two call sites from drifting.

The mapping is fixture-specific: it expects the 18-column layout of the
WY2627 ICT callup-status CSV. To ingest a different fixture, extend or
override ``CANONICAL_MAP`` at the call site.
"""

from __future__ import annotations

import re
from datetime import date

# Per-index column map: (csv_index, raw_name, canonical_name_or_None, store_in_extra_fields?)
# canonical_name=None means "no core mapping, extra_fields only".
CANONICAL_MAP: list[tuple[int, str, str | None, bool]] = [
    (0, "", "unit", False),
    (1, "Sub Unit 1", "sub_unit_1", False),
    (2, "Sub Unit 2", "sub_unit_2", False),
    (3, "Sub Unit 3", "sub_unit_3", False),
    (4, "Rank", "rank", False),
    (5, "Full Name", "full_name", False),
    (6, "Rank-Name", None, False),  # redundant composite, dropped
    (7, "Pers", "pers_no", False),  # external personnel number — canonical person identifier
    (8, "Callup Decision", None, True),
    (9, "Reason", None, True),
    (10, "Remarks", None, True),   # first Remarks column
    (11, "ORNS Yrs", None, True),
    (12, "HK ICT", None, True),
    (13, "NPI", None, True),
    (14, "SAR-21 Qual Date", None, True),
    (15, "Cbt Shoot History", None, True),
    (16, "Detail", None, True),
    (17, "Remarks", None, True),   # duplicate header; disambiguated below
]

# Keys for the two duplicate ``Remarks`` columns in extra_fields.
EXTRA_KEY_FOR_INDEX: dict[int, str] = {10: "remarks", 17: "remarks_2"}

# Core personnel columns mapped from canonical_name -> Personnel model attr.
CORE_ATTRS: dict[str, str] = {
    "unit": "unit",
    "sub_unit_1": "sub_unit_1",
    "sub_unit_2": "sub_unit_2",
    "sub_unit_3": "sub_unit_3",
    "rank": "rank",
    "full_name": "full_name",
    "pers_no": "pers_no",
}

# Inferred data types per column — used to populate ColumnMetadata.inferred_type.
INFERRED_TYPES: dict[str, str] = {
    "unit": "string",
    "sub_unit_1": "string",
    "sub_unit_2": "string",
    "sub_unit_3": "string",
    "rank": "string",
    "full_name": "string",
    "Pers": "string",
    "Rank-Name": "string",
    "Callup Decision": "string",
    "Reason": "string",
    "Remarks": "string",
    "ORNS Yrs": "integer",
    "HK ICT": "integer",
    "NPI": "string",
    "SAR-21 Qual Date": "date",
    "Cbt Shoot History": "integer",
    "Detail": "string",
}


def snake(key: str) -> str:
    """Normalize a raw CSV header to a snake_case extra_fields key."""
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def parse_caa_date(filename: str) -> date:
    """Extract CAA date from a filename token like ``caa260220`` -> 2026-02-20.

    Raises:
        ValueError: when no ``caaYYMMDD`` token is present.
    """
    m = re.search(r"caa(\d{2})(\d{2})(\d{2})", filename, re.IGNORECASE)
    if not m:
        raise ValueError(f"Could not parse CAA date from filename: {filename}")
    yy, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(2000 + yy, mm, dd)


def coerce_int(value: str) -> int | None:
    """Parse a string to int; empty/unparseable -> None."""
    v = value.strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def is_integer_column(raw_name: str) -> bool:
    """Whether ``raw_name`` should be coerced to int when stored in extra_fields."""
    return raw_name in {"ORNS Yrs", "HK ICT", "Cbt Shoot History"}
