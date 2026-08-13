"""Rank-to-category mapping for personnel.

Centralizes the SAF corps classification: every personnel rank maps to one of
two operational categories, ``Officer`` or ``WOSE`` (Warrant Officer, Specialist,
Enlistee). The category is inferred from rank at ingestion time and recomputed
whenever rank changes; it is never manually set.

Military Expert (MEX) ranks follow the SAF convention: ME1-ME3 are WOSE-tier,
ME4 and above are officer-tier.

**Quick Start:**
    from parade_state.utils import ranks

    category = ranks.category_for_rank("CPL")     # -> "WOSE"
    category = ranks.category_for_rank("CPT")     # -> "Officer"
    category = ranks.category_for_rank("ME2")     # -> "WOSE"
    category = ranks.category_for_rank("ME5")     # -> "Officer"

    # Unknown rank raises ValueError (strict validation)
    try:
        ranks.category_for_rank("SGT")
    except ValueError:
        ...

**Why Use This Module:**
- **Single Source of Truth**: the rank map lives here, not at call sites
- **Testable**: parametrized tests cover every rank in both lists
- **Deterministic**: category always follows rank, never manual input
"""

import re
from typing import Literal

PersonnelCategory = Literal["Officer", "WOSE"]

OFFICER_RANKS: frozenset[str] = frozenset(
    {"2LT", "LTA", "CPT", "MAJ", "LTC", "SLTC", "COL"}
)
"""SAF officer ranks (7)."""

WOSE_RANKS: frozenset[str] = frozenset(
    {
        "REC",
        "PTE",
        "LCP",
        "CPL",
        "CFC",
        "3SG",
        "2SG",
        "1SG",
        "SSG",
        "MSG",
        "3WO",
        "2WO",
        "1WO",
        "MWO",
    }
)
"""SAF WOSE ranks (14): Warrant Officer, Specialist, Enlistee."""

# Military Expert ranks are matched by pattern (ME1-ME3 -> WOSE, ME4+ -> Officer).
_ME_RE = re.compile(r"^ME(\d+)$")


def category_for_rank(rank: str) -> str:
    """Return the operational category (``Officer`` or ``WOSE``) for a rank.

    Input is trimmed and upper-cased before lookup. Military Expert ranks
    follow the SAF convention: ME1-ME3 are WOSE, ME4 and above are Officer.

    Raises:
        ValueError: if the rank is not a recognized SAF rank.
    """
    normalized = (rank or "").strip().upper()
    if not normalized:
        raise ValueError("Empty rank: cannot infer category")

    if normalized in OFFICER_RANKS:
        return "Officer"
    if normalized in WOSE_RANKS:
        return "WOSE"

    match = _ME_RE.match(normalized)
    if match:
        tier = int(match.group(1))
        if tier >= 1:
            return "WOSE" if tier <= 3 else "Officer"

    raise ValueError(f"Unrecognized rank: {rank!r}")


def is_known_rank(rank: str) -> bool:
    """Return True if the rank maps to a category, False otherwise."""
    try:
        category_for_rank(rank)
    except ValueError:
        return False
    return True
