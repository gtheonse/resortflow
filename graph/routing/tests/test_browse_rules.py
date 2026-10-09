"""Feature 011, T018 — browse recognition at tier 1
(`specs/011-browse-requests/contracts/browse-router-api.md`).

Covers: cue + plural-catalogue-noun recognition, the noun -> service map (incl. the `games` collision), strong
phrases, every veto, lookup-phrase precedence, FAQ / booking near-misses, the `{browse, book}` clause pair,
one-turn refinement carry-over, `stay safe` / `stay tuned`, and the SC-007 latency bound.

The phrases below are written from the contract and the reported defects, not read out of the vocabulary."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest
import yaml

from graph.models import BrowseFilters, DateWindow, Domain, RequestKind
from graph.routing.kinds import PreviousTurn, classify_kind
from graph.routing.matcher import match_services

TODAY = date(2026, 9, 30)  # Wednesday
D, E, W, S, G = Domain.DINING, Domain.ENTERTAINMENT, Domain.WELLNESS, Domain.STAY, Domain.GAMING


def _decide(text: str, previous: PreviousTurn | None = None):
    return classify_kind(text, match_services(text), previous=previous, today=TODAY)


BROWSE_CASES: list[tuple[str, list[Domain]]] = [
    # dining (the reported defect + noun map: venues -> dining even with no service word)
    ("what are the dining venues available in nevada", [D]),
    ("which restaurants do you have", [D]),
    ("list restaurants", [D]),
    ("list the restaurent options", [D]),
    ("any japanese restaurants tonight", [D]),
    ("show me the restaurants with vegetarian options", [D]),
    ("list venues", [D]),
    ("which venues are there", [D]),
    # events
    ("what events are happening in the next 5 days", [E]),
    ("any shows this weekend", [E]),
    ("what shows are on", [E]),
    ("list the concerts", [E]),
    ("what events are on tonight", [E]),
    ("which events are coming up", [E]),
    ("what games are on tonight", [E]),
    # stay period: "my stay" is a window marker, NOT the stay service
    ("what all events are happening in my stay period", [E]),
    ("what's on during my stay", [E]),
    ("any shows during my stay", [E]),
    ("concerts while I'm here", [E]),
    ("list the shows for my stay", [E]),
    # overview (no service -> the overview)
    ("list the resort experiences", []),
    ("what do you offer", []),
    ("what can I do here", []),
    ("what is there to do", []),
    ("what activities do you have", []),
    ("show me the options", []),
    # wellness
    ("what spa treatments do you have", [W]),
    ("list wellness services", [W]),
    ("which spa treatments are available", [W]),
    ("list the massage treatments", [W]),
    ("any spa treatments today", [W]),
    ("show me the golf and fitness options", [W]),
    # rooms
    ("what rooms are available oct 5 to oct 8", [S]),
    ("list the suites", [S]),
    ("which rooms do you have", [S]),
    ("show me the rooms for 3 nights", [S]),
    ("any suites available for 2 adults", [S]),
    ("what rooms are available next weekend", [S]),
    # gaming: `games` collides with the entertainment word `game`; a casino word decides
    ("what casino games do you have", [G]),
    ("what's on the casino floor", [G]),
    ("list the casino games", [G]),
    ("which poker games are there", [G]),
    ("what blackjack games do you have", [G]),
    ("show me the casino games", [G]),
]


@pytest.mark.parametrize("text,services", BROWSE_CASES)
def test_browse_kind_and_services(text: str, services: list[Domain]) -> None:
    decision = _decide(text)
    assert decision.kind == RequestKind.BROWSE, (text, decision)
    assert sorted(s.value for s in decision.services) == sorted(s.value for s in services), (text, decision.services)


def test_the_five_reported_phrases_are_decided_deterministically_at_tier_1() -> None:
    for text in (
        "what are the dining venues available in nevada",
        "list the resort experiences",
        "what all events are happening in my stay period",
        "what events are happening in the next 5 days",
    ):
        decision = _decide(text)
        assert decision.kind == RequestKind.BROWSE and decision.tier.name == "KIND_RULES", text
        assert decision.confidence >= 0.75, text


# --- near-misses: must NOT become browse ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "what time is checkout", "what time does the pool close", "what's the room rate",
        "do you have connecting rooms", "what time do the shows start", "what time does the spa open",
        "is parking available on site", "what's the wifi password policy", "how much is a suite",
        "when do the concerts start", "what are the pool hours today", "what is the dress code",
    ],
)
def test_property_questions_about_one_known_item_stay_faq(text: str) -> None:
    decision = _decide(text)
    assert decision.kind != RequestKind.BROWSE, (text, decision)
    assert decision.kind == RequestKind.FAQ, (text, decision)


@pytest.mark.parametrize(
    "text",
    [
        "book a table for 2 tomorrow", "get me tickets to a show", "I'd like a room for 3 nights",
        "reserve a spa treatment tomorrow at 3pm", "can I get a table for 4 tonight",
        "what shows can I book", "list the restaurants and reserve one",
    ],
)
def test_explicit_booking_cues_veto_browse(text: str) -> None:
    decision = _decide(text)
    assert decision.kind != RequestKind.BROWSE, (text, decision)


@pytest.mark.parametrize(
    "text",
    ["what have I booked", "list my bookings", "is my dinner confirmed", "what time is my dinner reservation",
     "did I book a show", "show me my itinerary"],
)
def test_lookups_stay_lookups(text: str) -> None:
    decision = _decide(text)
    assert decision.kind == RequestKind.LOOKUP, (text, decision)


def test_cancel_and_change_are_still_modify() -> None:
    assert _decide("cancel my dinner").kind == RequestKind.MODIFY
    assert _decide("change the party size on our table booking").kind == RequestKind.MODIFY


def test_stay_safe_and_stay_tuned_are_still_not_requests() -> None:
    for text in ("please stay safe during the storm tonight", "stay tuned for updates on the event schedule"):
        decision = _decide(text)
        assert decision.kind != RequestKind.BROWSE, text
        assert Domain.STAY not in decision.services, text


# --- filters and window on the decision ------------------------------------------------------------


def test_filters_and_window_are_populated_on_the_decision() -> None:
    decision = _decide("any japanese restaurants tonight")
    assert decision.browse_filters is not None and decision.browse_filters.cuisine == "japanese"
    assert decision.window == DateWindow(start=TODAY, end=TODAY, source="stated")

    decision = _decide("what events are happening in the next 5 days")
    assert decision.window == DateWindow(start=date(2026, 9, 30), end=date(2026, 10, 4), source="stated")


def test_stay_period_phrases_carry_the_needs_dates_marker() -> None:
    decision = _decide("what's on during my stay")
    assert decision.window is not None and decision.window.source == "needs_dates"


def test_overview_has_no_window_and_no_filters_worth_reporting() -> None:
    decision = _decide("list the resort experiences")
    assert decision.window is None
    assert decision.browse_filters in (None, BrowseFilters())


def test_non_browse_decisions_carry_neither_field() -> None:
    decision = _decide("book a table for 2 tomorrow")
    assert decision.browse_filters is None and decision.window is None


# --- clause pair -----------------------------------------------------------------------------------


def test_browse_plus_book_pair_uses_secondary_kind() -> None:
    decision = _decide("show me the restaurants, and book the steakhouse for 2 tomorrow")
    assert decision.kind == RequestKind.BROWSE
    assert decision.secondary_kind == RequestKind.BOOK
    assert Domain.DINING in decision.secondary_services


# --- one-turn refinement (FR-005a) ------------------------------------------------------------------


def _prev_browse(services: list[Domain], *, filters: BrowseFilters | None = None, window: DateWindow | None = None) -> PreviousTurn:
    return PreviousTurn(kind=RequestKind.BROWSE, services=services, had_booking=False, filters=filters, window=window)


def test_only_japanese_narrows_the_previous_dining_browse() -> None:
    decision = _decide("only japanese", _prev_browse([D]))
    assert decision.kind == RequestKind.BROWSE and decision.carried_over
    assert decision.services == [D]
    assert decision.browse_filters is not None and decision.browse_filters.cuisine == "japanese"


def test_cheaper_ones_applies_a_price_cap() -> None:
    decision = _decide("cheaper ones", _prev_browse([D]))
    assert decision.kind == RequestKind.BROWSE and decision.browse_filters.price_band == "<=$$"


def test_new_filter_merges_with_the_previous_one() -> None:
    previous = _prev_browse([D], filters=BrowseFilters(cuisine="japanese"))
    decision = _decide("cheaper ones", previous)
    assert decision.browse_filters.cuisine == "japanese" and decision.browse_filters.price_band == "<=$$"


def test_a_new_date_replaces_the_previous_window() -> None:
    previous = _prev_browse([E], window=DateWindow(start=date(2026, 9, 30), end=date(2026, 10, 4), source="stated"))
    decision = _decide("what about this weekend", previous)
    assert decision.kind == RequestKind.BROWSE and decision.services == [E]
    assert decision.window == DateWindow(start=date(2026, 10, 3), end=date(2026, 10, 4), source="stated")


def test_how_about_next_week_and_only_concerts() -> None:
    assert _decide("how about next week", _prev_browse([E])).window.start == date(2026, 10, 5)
    only = _decide("only concerts", _prev_browse([E]))
    assert only.browse_filters.category == "concert"


def test_dates_given_after_a_needs_dates_browse_complete_it() -> None:
    previous = _prev_browse([E], window=DateWindow(start=TODAY, end=TODAY, source="needs_dates"))
    decision = _decide("oct 5 to oct 8", previous)
    assert decision.kind == RequestKind.BROWSE and decision.carried_over
    assert decision.window == DateWindow(start=date(2026, 10, 5), end=date(2026, 10, 8), source="stated")


def test_refinement_is_one_turn_only() -> None:
    for kind in (RequestKind.SMALLTALK, RequestKind.FAQ, RequestKind.BOOK, None):
        previous = PreviousTurn(kind=kind, services=[D], had_booking=False)
        assert _decide("only japanese", previous).kind != RequestKind.BROWSE, kind


def test_a_refinement_cue_with_no_previous_browse_is_not_a_browse() -> None:
    assert _decide("only japanese").kind != RequestKind.BROWSE
    assert _decide("cheaper ones").kind != RequestKind.BROWSE


def test_unrelated_message_after_a_browse_is_not_treated_as_a_refinement() -> None:
    assert _decide("thanks", _prev_browse([D])).kind == RequestKind.SMALLTALK
    assert _decide("book a table for 2 tomorrow", _prev_browse([D])).kind == RequestKind.BOOK


# --- SC-007 ------------------------------------------------------------------------------------------

_CASES = Path(__file__).resolve().parents[3] / "eval" / "data" / "routing_cases.yaml"


def test_median_browse_routing_time_is_under_five_milliseconds() -> None:
    texts = [
        c["text"] for c in yaml.safe_load(_CASES.read_text(encoding="utf-8"))["cases"]
        if c["category"].startswith("browse_") and c["split"] == "dev" and "previous" not in c
    ]
    assert texts
    samples = []
    for text in texts * 3:
        start = time.perf_counter()
        classify_kind(text, match_services(text), today=TODAY)
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    assert samples[len(samples) // 2] < 5.0, f"median {samples[len(samples) // 2]:.3f} ms"
