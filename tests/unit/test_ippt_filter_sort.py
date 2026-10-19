"""Unit tests: dashboard filter/sort views (services.ippt.filter_states /
sort_states) — pure functions over the dashboard payload's entry dicts,
no database involved."""

import copy

from parade_state.services.ippt import (
    BOOKED_FILTERS,
    SORT_KEYS,
    filter_states,
    sort_states,
)


def row(
    name: str,
    *,
    sub_unit: str = "BN HQ",
    state: str = "ippt_not_attempted",
    state_label: str = "Not attempted",
    booked: bool | None = None,
    ffi: str | None = None,
    window_end: str | None = None,
    days_to_close: int | None = None,
) -> dict:
    return {
        "full_name": name,
        "sub_unit": sub_unit,
        "state": state,
        "state_label": state_label,
        "booked": booked,
        "ffi": ffi,
        "window_end": window_end,
        "days_to_close": days_to_close,
    }


ROSTER = [
    row(
        "ALPHA", booked=True, ffi="pending", window_end="2027-07-08", days_to_close=300
    ),
    row(
        "BRAVO",
        sub_unit="ALPHA WING",
        booked=False,
        ffi="na",
        window_end="2027-07-28",
        days_to_close=320,
    ),
    row(
        "CHARLIE",
        state="nsfit_not_started",
        state_label="Mandatory NS FIT — not started",
    ),
    row("DELTA", state="met_award", state_label="Met — IPPT award"),
]


def names(entries: list[dict]) -> list[str]:
    return [entry["full_name"] for entry in entries]


class TestFilterStates:
    def test_no_filters_keeps_everything(self):
        assert filter_states(ROSTER) is ROSTER
        assert (
            filter_states(ROSTER, subunit=None, state=None, booked=None, ffi=None)
            == ROSTER
        )

    def test_subunit_exact_match(self):
        assert names(filter_states(ROSTER, subunit="ALPHA WING")) == ["BRAVO"]

    def test_state_key_match(self):
        assert names(filter_states(ROSTER, state="nsfit_not_started")) == ["CHARLIE"]

    def test_filters_combine_with_and(self):
        kept = filter_states(ROSTER, subunit="BN HQ", booked="yes")
        assert names(kept) == ["ALPHA"]

    def test_booked_tri_state(self):
        assert BOOKED_FILTERS == {"yes": True, "no": False, "unknown": None}
        assert names(filter_states(ROSTER, booked="yes")) == ["ALPHA"]
        assert names(filter_states(ROSTER, booked="no")) == ["BRAVO"]
        assert names(filter_states(ROSTER, booked="unknown")) == ["CHARLIE", "DELTA"]

    def test_ffi_values_including_none(self):
        assert names(filter_states(ROSTER, ffi="pending")) == ["ALPHA"]
        assert names(filter_states(ROSTER, ffi="na")) == ["BRAVO"]
        assert names(filter_states(ROSTER, ffi="fit")) == []
        assert names(filter_states(ROSTER, ffi="none")) == ["CHARLIE", "DELTA"]

    def test_unknown_values_do_not_filter(self):
        # The route pre-validates, but the guard must not lose rows even
        # if a raw value slips through.
        assert filter_states(ROSTER, booked="bogus") == ROSTER
        assert filter_states(ROSTER, ffi="bogus") == ROSTER


class TestSortStates:
    def test_urgency_default_matches_days_left(self):
        assert SORT_KEYS[0] == "urgency"
        assert names(sort_states(ROSTER)) == names(sort_states(ROSTER, "days_left"))

    def test_days_left_ascending_nulls_last_name_tiebreak(self):
        entries = [
            row("ZULU", days_to_close=None),
            row("YANKEE", days_to_close=50),
            row("MIKE", days_to_close=50),
            row("KILO", days_to_close=10),
        ]
        assert names(sort_states(entries, "days_left")) == [
            "KILO",
            "MIKE",
            "YANKEE",
            "ZULU",
        ]

    def test_subunit_alphabetical_then_name(self):
        assert names(sort_states(ROSTER, "subunit")) == [
            "BRAVO",  # ALPHA WING
            "ALPHA",  # BN HQ
            "CHARLIE",
            "DELTA",
        ]

    def test_state_sorts_by_display_label(self):
        assert names(sort_states(ROSTER, "state")) == [
            "CHARLIE",  # "Mandatory NS FIT — not started"
            "DELTA",  # "Met — IPPT award"
            "ALPHA",  # "Not attempted"
            "BRAVO",
        ]

    def test_window_end_ascending_nulls_last(self):
        assert names(sort_states(ROSTER, "window_end")) == [
            "ALPHA",  # 2027-07-08
            "BRAVO",  # 2027-07-28
            "CHARLIE",  # no window
            "DELTA",
        ]

    def test_input_list_is_not_mutated(self):
        entries = copy.deepcopy(ROSTER)
        base_order = names(entries)
        sort_states(entries, "subunit")
        assert names(entries) == base_order
