"""Feature 011, T016 — `graph.routing.browse` helpers: filter extraction, stay-period wording, window
resolution (`specs/011-browse-requests/contracts/browse-router-api.md`).

Must fail before T017 (the implementation) — the module is a skeleton until then."""

from __future__ import annotations

from datetime import date

import pytest

from graph.models import BrowseFilters, DateWindow, DietaryRestriction
from graph.routing.browse import extract_filters, resolve_window, wants_stay_period

TODAY = date(2026, 9, 30)  # Wednesday


@pytest.mark.parametrize(
    "text,cuisine",
    [
        ("any japanese restaurants tonight", "japanese"),
        ("list the sushi places", "japanese"),
        ("which steakhouse do you have", "steakhouse"),
        ("mexican restaurants", "mexican"),
        ("any italian restaurants", "italian"),
        ("seafood options", "seafood"),
        ("asian fusion restaurants", "asian_fusion"),
    ],
)
def test_cuisine(text: str, cuisine: str) -> None:
    assert extract_filters(text).cuisine == cuisine


@pytest.mark.parametrize(
    "text,band",
    [
        ("restaurants at $$", "$$"),
        ("restaurants at $$$$", "$$$$"),
        ("cheap restaurants", "<=$$"),
        ("cheaper ones", "<=$$"),
        ("budget options", "<=$$"),
        ("under $$$", "<=$$"),
        ("upscale restaurants", ">=$$$"),
    ],
)
def test_price_band(text: str, band: str) -> None:
    assert extract_filters(text).price_band == band


@pytest.mark.parametrize(
    "text,restriction",
    [
        ("restaurants with vegetarian options", DietaryRestriction.VEGETARIAN),
        ("vegan restaurants", DietaryRestriction.VEGAN),
        ("places safe for a peanut allergy", DietaryRestriction.PEANUT),
        ("any gluten free restaurants", DietaryRestriction.GLUTEN),
        ("halal options", DietaryRestriction.HALAL),
        ("kosher restaurants", DietaryRestriction.KOSHER),
        ("low sodium options", DietaryRestriction.LOW_SODIUM),
    ],
)
def test_dietary_is_the_enum_never_free_text(text: str, restriction: DietaryRestriction) -> None:
    assert extract_filters(text).dietary == restriction


def test_vegetarian_is_dietary_not_cuisine() -> None:
    """"restaurants with vegetarian options" narrows by the structured restriction (and so triggers the
    "state it when you book" line), not by the venue cuisine of the same name."""
    filters = extract_filters("restaurants with vegetarian options")
    assert filters.dietary == DietaryRestriction.VEGETARIAN
    assert filters.cuisine is None


@pytest.mark.parametrize(
    "text,category",
    [
        ("what concerts are on", "concert"),
        ("any comedy shows", "comedy"),
        ("sports events this weekend", "sports"),
        ("what spa treatments do you have", "spa"),
        ("list the massage treatments", "spa"),
        ("golf options", "golf"),
        ("fitness classes", "fitness"),
        ("cabana rentals", "cabana"),
    ],
)
def test_category(text: str, category: str) -> None:
    assert extract_filters(text).category == category


def test_generic_show_or_event_is_not_a_category() -> None:
    assert extract_filters("what shows are on").category is None
    assert extract_filters("what events are happening").category is None


@pytest.mark.parametrize(
    "text,party",
    [("rooms for 2 adults", 2), ("a table for 4", 4), ("party of 6", 6), ("what rooms are available for 3 guests", 3)],
)
def test_party_size(text: str, party: int) -> None:
    assert extract_filters(text).party_size == party


def test_no_filters_is_all_none() -> None:
    assert extract_filters("list the resort experiences") == BrowseFilters()


def test_filters_never_read_a_dietary_word_out_of_a_longer_word() -> None:
    assert extract_filters("what a delicious eggplant dish").dietary is None  # "egg" only as a whole token


# --- stay-period wording -------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "what all events are happening in my stay period", "what's on during my stay", "concerts while I'm here",
        "any shows during my stay", "list the shows for my stay", "events in my stay",
    ],
)
def test_wants_stay_period(text: str) -> None:
    assert wants_stay_period(text) is True


@pytest.mark.parametrize(
    "text", ["what events are happening in the next 5 days", "please stay safe", "stay tuned for updates", "list the suites"]
)
def test_does_not_want_stay_period(text: str) -> None:
    assert wants_stay_period(text) is False


# --- window resolution -----------------------------------------------------------------


def test_stated_window() -> None:
    window = resolve_window("what events are happening in the next 5 days", today=TODAY, wants_stay_period=False)
    assert window == DateWindow(start=date(2026, 9, 30), end=date(2026, 10, 4), source="stated")


def test_stay_period_with_no_dates_needs_dates() -> None:
    window = resolve_window("what's on during my stay", today=TODAY, wants_stay_period=True)
    assert window is not None and window.source == "needs_dates"


def test_explicit_dates_win_over_the_stay_period_marker() -> None:
    window = resolve_window("events during my stay oct 5 to oct 8", today=TODAY, wants_stay_period=True)
    assert window == DateWindow(start=date(2026, 10, 5), end=date(2026, 10, 8), source="stated")


def test_no_window_words_gives_none() -> None:
    assert resolve_window("what events are on", today=TODAY, wants_stay_period=False) is None
    assert resolve_window("list the restaurants", today=TODAY, wants_stay_period=False) is None
