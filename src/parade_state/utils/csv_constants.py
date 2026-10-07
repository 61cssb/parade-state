"""CSV ingestion contract v2 (issue 34): header-name-based column matching.

Single source of truth for the upload contract used by both the app-side
CSV process endpoint (``parade_state.api.csv_upload``) and the standalone
demo ingester (``experiments/csv_to_nr/ingest.py``). Lifting these into a
shared module prevents the two call sites from drifting.

Contract (signed off 2026-08-24):

- Columns are matched by **header name** (exact match after stripping
  surrounding whitespace; the first occurrence of a name wins), not by
  position. Extra columns are tolerated and ignored.
- Required headers: ``Unit``, ``Sub Unit 1-3``, ``Rank``, ``Full Name``,
  ``Callup Decision``, ``Reason``, ``Remarks``, ``HK ICT`` and ``ORNS``
  (alias ``ORNS Yrs``). A missing required header — including a blank
  ``Unit`` header — rejects the upload with an error naming the column.
- Optional headers: ``Pers`` (stored to ``pers_no``; absent/blank → NULL)
  and ``Age(Yr)`` (stored to ``extra_fields.age_yr``).
- Row filter: only rows whose ``Callup Decision`` is ``Yes`` (strict but
  case-insensitive) are stored; anything else — ``No``, blank, ``Y``, free
  text — is skipped and counted. ``Callup Decision`` and ``Reason`` are
  read but never stored.
- Storage: ``Remarks`` (first ``Remarks`` column only) →
  ``personnel.remarks``; ``ORNS``/``ORNS Yrs`` → ``extra_fields.orns`` and
  ``HK ICT`` → ``extra_fields.hk_ict`` (int years / int).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# (field, accepted header names, required?) — resolution order is the
# storage/contract order. First accepted name is the canonical spelling
# used in error messages.
_HEADER_SPEC: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("unit", ("Unit",), True),
    ("sub_unit_1", ("Sub Unit 1",), True),
    ("sub_unit_2", ("Sub Unit 2",), True),
    ("sub_unit_3", ("Sub Unit 3",), True),
    ("rank", ("Rank",), True),
    ("full_name", ("Full Name",), True),
    ("pers_no", ("Pers",), False),
    ("callup_decision", ("Callup Decision",), True),
    ("reason", ("Reason",), True),
    ("remarks", ("Remarks",), True),
    ("orns", ("ORNS Yrs", "ORNS"), True),
    ("hk_ict", ("HK ICT",), True),
    ("age_yr", ("Age(Yr)",), False),
)

# Core personnel columns: canonical field -> Personnel model attr.
CORE_ATTRS: dict[str, str] = {
    "unit": "unit",
    "sub_unit_1": "sub_unit_1",
    "sub_unit_2": "sub_unit_2",
    "sub_unit_3": "sub_unit_3",
    "rank": "rank",
    "full_name": "full_name",
    "pers_no": "pers_no",
}

# Fields stored in Personnel.extra_fields (as ints; blank cell → None,
# absent column → key omitted entirely).
EXTRA_INT_FIELDS: frozenset[str] = frozenset({"orns", "hk_ict", "age_yr"})

# Fields that are required and read but never stored anywhere.
READ_ONLY_FIELDS: frozenset[str] = frozenset({"callup_decision", "reason"})

# Canonical fields the contract requires (drives ColumnMetadata.is_required).
REQUIRED_FIELDS: frozenset[str] = frozenset(
    field_name for field_name, _, required in _HEADER_SPEC if required
)

# Inferred data types per canonical field — used to populate
# ColumnMetadata.inferred_type; unmapped (extra) columns default to string.
INFERRED_TYPES: dict[str, str] = {
    "unit": "string",
    "sub_unit_1": "string",
    "sub_unit_2": "string",
    "sub_unit_3": "string",
    "rank": "string",
    "full_name": "string",
    "pers_no": "string",
    "callup_decision": "string",
    "reason": "string",
    "remarks": "string",
    "orns": "integer",
    "hk_ict": "integer",
    "age_yr": "integer",
}


class MissingColumnsError(ValueError):
    """A required CSV header is absent (or blank, e.g. the pre-fix export's
    missing ``Unit`` header). ``missing`` carries the display names."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        names = ", ".join(repr(n) for n in missing)
        super().__init__(f"CSV is missing required column(s): {names}")


@dataclass
class ResolvedColumns:
    """Result of matching a CSV header row against the v2 contract.

    ``field_index`` maps canonical field → CSV column index for every
    matched field (required fields always present; optional fields only
    when the file carries the column). ``canonical_for_index`` maps CSV
    column index → canonical field for *stored* columns only (Callup
    Decision / Reason are read-only; unmatched extras are ignored).
    """

    field_index: dict[str, int] = field(default_factory=dict)
    canonical_for_index: dict[int, str] = field(default_factory=dict)

    def has(self, field: str) -> bool:
        return field in self.field_index


def resolve_columns(header: list[str]) -> ResolvedColumns:
    """Match ``header`` against the v2 contract by name.

    Raises:
        MissingColumnsError: naming every required column that is absent
            (a blank header matches nothing, so the pre-fix export's empty
            ``Unit`` header surfaces as a missing ``Unit`` column).
    """
    normalized = [name.strip() for name in header]
    resolved = ResolvedColumns()
    missing: list[str] = []

    for field_name, accepted, required in _HEADER_SPEC:
        index = next(
            (i for i, name in enumerate(normalized) if name in accepted),
            None,
        )
        if index is None:
            if required:
                display = accepted[0]
                if len(accepted) > 1:
                    display = " / ".join(accepted)
                missing.append(display)
            continue
        resolved.field_index[field_name] = index
        if field_name not in READ_ONLY_FIELDS:
            resolved.canonical_for_index[index] = field_name

    if missing:
        raise MissingColumnsError(missing)
    return resolved


def is_callup_yes(value: str) -> bool:
    """Strict-but-case-insensitive row filter: only an exact ``yes``
    (casefolded) decision stores the row; blank / ``Y`` / free text skip."""
    return value.strip().casefold() == "yes"


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
