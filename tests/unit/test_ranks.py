"""Unit tests for the rank-to-category mapping utility.

Covers every rank in both lists, the Military Expert (MEX) rule, and the
strict-validation behavior for unrecognized ranks. This satisfies the issue #10
acceptance criterion: "every rank maps correctly" plus an unknown-rank case.
"""

import pytest

from parade_state.utils import ranks


@pytest.mark.parametrize("rank", list(ranks.OFFICER_RANKS))
def test_officer_ranks_map_to_officer(rank: str) -> None:
    assert ranks.category_for_rank(rank) == "Officer"


@pytest.mark.parametrize("rank", list(ranks.WOSE_RANKS))
def test_wose_ranks_map_to_wose(rank: str) -> None:
    assert ranks.category_for_rank(rank) == "WOSE"


@pytest.mark.parametrize("rank", ["ME1", "ME2", "ME3"])
def test_me1_to_me3_are_wose(rank: str) -> None:
    assert ranks.category_for_rank(rank) == "WOSE"


@pytest.mark.parametrize("rank", ["ME4", "ME5", "ME6", "ME9"])
def test_me4_and_above_are_officer(rank: str) -> None:
    assert ranks.category_for_rank(rank) == "Officer"


def test_normalizes_whitespace_and_case() -> None:
    assert ranks.category_for_rank("  cpt  ") == "Officer"
    assert ranks.category_for_rank("cpl") == "WOSE"


@pytest.mark.parametrize(
    "rank",
    [
        "",          # empty
        "   ",       # whitespace only
        "SGT",       # common but not a SAF rank in our lists
        "GENERAL",   # not in the SAF system here
        "ME0",       # below the ME tier floor
        "MEME",      # looks like ME prefix but malformed
    ],
)
def test_unrecognized_rank_raises_value_error(rank: str) -> None:
    with pytest.raises(ValueError):
        ranks.category_for_rank(rank)


@pytest.mark.parametrize(
    "rank, expected",
    [
        ("LTA", True),
        ("CPL", True),
        ("ME3", True),
        ("SGT", False),
        ("", False),
    ],
)
def test_is_known_rank(rank: str, expected: bool) -> None:
    assert ranks.is_known_rank(rank) is expected
