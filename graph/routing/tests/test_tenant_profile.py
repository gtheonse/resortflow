"""Feature 011, T016a — `TenantProfile` / `detect_other_property` (data-model.md, FR-007): the guest's own
property comes from the verified tenant, never from message text; a *different* property named in the text
only adds a sentence, it never changes the catalogue read.

Must fail before T017a."""

from __future__ import annotations

import pytest

from graph.routing.browse import PROPERTY_NAMES, detect_other_property


def test_property_names_per_tenant() -> None:
    assert PROPERTY_NAMES == {"MGV": "Meridian Grand", "MHB": "Meridian Harbor"}


@pytest.mark.parametrize(
    "text,tenant,expected",
    [
        ("dining in Boston", "MGV", "Meridian Harbor"),
        ("what are the restaurants in massachusetts", "MGV", "Meridian Harbor"),
        ("show me restaurants in las vegas", "MHB", "Meridian Grand"),
        ("what shows are on in vegas", "MHB", "Meridian Grand"),
        ("events in nevada", "MHB", "Meridian Grand"),
    ],
)
def test_names_the_other_property(text: str, tenant: str, expected: str) -> None:
    assert detect_other_property(text, tenant) == expected


@pytest.mark.parametrize(
    "text,tenant",
    [
        ("what are the dining venues available in nevada", "MGV"),
        ("restaurants in las vegas", "MGV"),
        ("what are the dining venues available in boston", "MHB"),
        ("restaurants in massachusetts", "MHB"),
        ("list the restaurants", "MGV"),
        ("list the restaurants", "MHB"),
    ],
)
def test_own_or_no_region_words_give_none(text: str, tenant: str) -> None:
    assert detect_other_property(text, tenant) is None


def test_a_message_naming_both_returns_none_the_guests_own_wins() -> None:
    assert detect_other_property("restaurants in boston or las vegas", "MGV") is None
    assert detect_other_property("restaurants in boston or las vegas", "MHB") is None


@pytest.mark.parametrize("text", ["please ask the manager", "dinner for two", "ma'am, a table please", "what a great show", "nvidia keynote"])
def test_short_region_codes_only_match_as_whole_tokens(text: str) -> None:
    assert detect_other_property(text, "MGV") is None
    assert detect_other_property(text, "MHB") is None


def test_unknown_tenant_returns_none_and_the_text_never_selects_a_tenant() -> None:
    assert detect_other_property("dining in Boston", "XXX") is None
    assert detect_other_property("dining in Boston", "") is None
