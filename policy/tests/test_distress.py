"""T018 — corpus + property tests for `check_distress_signal` (ADR-036, FR-004, FR-018).

No model judgment participates (constitution) — this is a pure function
over a maintained corpus, so its tests are ordinary example-based and
property-based tests, not integration tests against any vendor.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from policy.distress import (
    AMBIGUOUS_THRESHOLD,
    CONFIDENT_THRESHOLD,
    DISTRESS_CORPUS,
    check_distress_signal,
)


@pytest.mark.parametrize("corpus_text", list(DISTRESS_CORPUS.values()))
def test_every_corpus_entry_flags_itself(corpus_text: str) -> None:
    """The exact/fuzzy substring layer guarantees a corpus entry always
    matches itself with similarity 1.0 — always confidently flagged, never
    merely ambiguous."""
    result = check_distress_signal(corpus_text)
    assert result.flagged is True
    assert result.ambiguous is False


@pytest.mark.parametrize(
    "clean_utterance",
    [
        "what time does the casino floor open",
        "can you book me a dinner reservation for two",
        "what's the weather like this weekend",
        "I'd like to check out late tomorrow",
    ],
)
def test_ordinary_amenity_requests_are_not_flagged(clean_utterance: str) -> None:
    result = check_distress_signal(clean_utterance)
    assert result.flagged is False
    assert result.ambiguous is False


def test_informational_rg_question_is_distinct_from_distress() -> None:
    """A guest asking ABOUT responsible-gaming resources is not itself a
    distress signal — that's the `rg_status` lookup intent
    (`agents/gaming/nodes/intent_parser.py`'s `_RG_KEYWORDS`), a different,
    lower-stakes classification than this module's."""
    result = check_distress_signal("what are your responsible gaming resources")
    assert result.flagged is False
    assert result.ambiguous is False


def test_no_outcome_is_ever_flagged_and_ambiguous_simultaneously() -> None:
    """FR-018: there is no fourth state — every result is exactly one of
    {confident, ambiguous, clean}."""
    for text in [*DISTRESS_CORPUS.values(), "what time is checkout", "hello there"]:
        result = check_distress_signal(text)
        assert not (result.flagged and result.ambiguous)


@settings(deadline=None)  # embedding-model inference is slower than hypothesis's
                          # default 200ms per-example budget; arbitrary fuzzed text
                          # rarely hits the fast substring-match path other tests do
@given(st.text(min_size=0, max_size=200))
def test_never_raises_on_arbitrary_input(text: str) -> None:
    """A pure function — must resolve to a value for any string input,
    including empty strings, whitespace, or unusual unicode, never raise."""
    result = check_distress_signal(text)
    assert isinstance(result.flagged, bool)
    assert isinstance(result.ambiguous, bool)


def test_thresholds_are_ordered() -> None:
    """A basic sanity property on the two threshold constants themselves —
    the ambiguous band must be a strict subset below the confident one."""
    assert 0.0 < AMBIGUOUS_THRESHOLD < CONFIDENT_THRESHOLD <= 1.0
