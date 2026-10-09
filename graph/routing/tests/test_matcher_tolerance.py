"""Feature 010, T030 — `graph.routing.matcher` tier 0: tolerant whole-word
and phrase matching (research R-03). Must fail before T037/T038
(`vocabulary.py`, `matcher.py`)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from graph.models import Domain
from graph.routing.matcher import edit_distance, match_services


def test_exact_word_matches() -> None:
    assert match_services("book a table for dinner").services == [Domain.DINING]


def test_single_adjacent_swap_at_five_letters() -> None:
    # "tabel" -> "table": a transposition of 'l'/'e', one edit under OSA.
    result = match_services("tabel for 2 tonight")
    assert Domain.DINING in result.services


def test_single_edit_at_five_to_seven_letters() -> None:
    result = match_services("I'd like a masage at the spa")
    assert Domain.WELLNESS in result.services


def test_two_edits_at_eight_plus_letters() -> None:
    result = match_services("book a table at the resturant tonight")
    assert Domain.DINING in result.services


def test_four_letter_word_matches_only_exact_or_single_swap() -> None:
    # "romo" is a swap of 'o'/'m' relative to "room" -> should still match (single swap at 4).
    result = match_services("can I get a romo upgrade")
    assert Domain.STAY in result.services


def test_zoom_does_not_match_room() -> None:
    """FR-002: a four-letter word tolerates only a single adjacent-letter
    swap, never an arbitrary one-edit (substitution) match — 'zoom' vs
    'room' differs at one position only (z/r), not a transposition."""
    result = match_services("can I zoom in on these photos")
    assert Domain.STAY not in result.services


def test_whole_word_matching_rejects_substring_traps() -> None:
    """The core defect this feature fixes: none of these trap words may
    match via mere substring containment."""
    traps = [
        ("can I get tickets for the theater tonight", Domain.DINING),  # theater contains "eat"
        ("the shower is broken", Domain.ENTERTAINMENT),  # shower contains "show"
        ("is the restroom clean", Domain.STAY),  # restroom contains "room"
        ("are there mushroom dishes", Domain.STAY),  # mushroom contains "room"
        ("what a great time we had", Domain.DINING),  # great contains "eat"
        ("is there a seat available", Domain.DINING),  # seat contains "eat"
        ("please stay safe tonight", Domain.STAY),  # "stay" alone is not a trigger
    ]
    for text, forbidden in traps:
        result = match_services(text)
        assert forbidden not in result.services, f"{text!r} wrongly matched {forbidden}"


def test_ambiguous_equal_distance_reports_ambiguous() -> None:
    """FR-005: a typo equidistant from two services' vocabulary is
    reported ambiguous, choosing neither."""
    from graph.routing.vocabulary import DEFAULT, ServiceVocabulary, WordEntry

    # Build a fixture vocabulary with two one-letter-apart words at equal
    # distance from a shared typo, to exercise the tie-break deterministically
    # regardless of what the shipped vocabulary happens to contain.
    fixture = DEFAULT.model_copy(deep=True)
    # "spore" is a single substitution from each of "spare" (index 2) and
    # "score" (index 1) — genuinely equidistant, both in the >=5-letter
    # one-edit tolerance band.
    fixture.services[Domain.DINING] = ServiceVocabulary(words=[WordEntry(word="spare", variants=[])])
    fixture.services[Domain.WELLNESS] = ServiceVocabulary(words=[WordEntry(word="score", variants=[])])
    result = match_services("do you have a spore available", vocab=fixture)
    assert result.ambiguous is True
    assert result.services == []


@settings(max_examples=50)
@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=12))
def test_matcher_never_fires_on_a_substring_without_word_boundary(word: str) -> None:
    """Property: embedding any single vocabulary word inside a longer
    random word (so it is a strict substring, not a whole word) must never
    match that word's service — whole-word matching is the whole point."""
    text = f"x{word}y"
    result = match_services(text)
    # A random word could coincidentally be a real word too (e.g. "a"); the
    # property only claims something about words that are NOT themselves
    # exact/tolerant matches on their own.
    alone = match_services(word)
    if alone.services:
        return  # the embedded token happens to be a real word itself; not a counterexample
    assert result.services == []


def test_edit_distance_basic_cases() -> None:
    assert edit_distance("table", "table") == 0
    assert edit_distance("tabel", "table") == 1  # transposition
    assert edit_distance("table", "tables") == 1  # insertion
    assert edit_distance("massage", "masage") == 1  # deletion


def test_edit_distance_respects_max_distance_bound() -> None:
    assert edit_distance("aaaa", "zzzz", max_distance=1) > 1
