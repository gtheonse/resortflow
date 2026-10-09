"""Feature 011 — browse helpers (`specs/011-browse-requests/contracts/browse-router-api.md`).

Pure functions: no I/O, no model, no tool import (the structural test over `graph/routing/*.py` keeps
passing). Filter vocabularies live in `vocabulary.yaml` (`kinds.browse.filters`), reviewable as data like
the service vocabulary (FR-004 of feature 010)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from graph.models import BrowseFilters, DateWindow, DietaryRestriction
from graph.routing.matcher import phrase_present, tokenize
from graph.routing.temporal import parse_date_range
from graph.routing.vocabulary import DEFAULT, Vocabulary

_PARTY_RE = re.compile(
    r"\b(?:for|party of)\s+(\d{1,2})\b(?!\s*(?:nights?|days?|weeks?|am\b|pm\b|:))", re.IGNORECASE
)
_PRICE_SYMBOL_RE = re.compile(r"(?<![\w$])(\${1,4})(?![\w$])")
_BEFORE_UNDER_RE = re.compile(r"(?:under|below|less than|at most|max(?:imum)?(?: of)?)\s*$", re.IGNORECASE)
_BEFORE_OVER_RE = re.compile(r"(?:over|above|more than|at least|min(?:imum)?(?: of)?)\s*$", re.IGNORECASE)

_STAY_PERIOD_PHRASES: tuple[str, ...] = (
    "my stay", "our stay", "my stay period", "during my stay", "during our stay", "for my stay", "for our stay",
    "in my stay", "in our stay", "while i m here", "while i am here", "while we re here", "while we are here",
    "during my visit", "during our visit",
)
"""Window markers, not a request for the `stay` service (feature 010's `my stay` service phrase still
matches the service vocabulary; the browse node ignores it when this says the guest means a window)."""


def _filters_cfg(vocab: Vocabulary) -> dict[str, Any]:
    cfg = vocab.kinds.get("browse", {})
    filters = cfg.get("filters", {}) if isinstance(cfg, dict) else {}
    return filters if isinstance(filters, dict) else {}


def _first_match(mapping: dict[str, list[str]], tokens: list[str]) -> str | None:
    token_set = set(tokens)
    for key, words in mapping.items():
        for word in words:
            if (" " in word and phrase_present(tokens, word)) or word in token_set:
                return key
    return None


def _price_band(text: str, tokens: list[str], cfg: dict[str, Any]) -> str | None:
    match = _PRICE_SYMBOL_RE.search(text)
    if match:
        length = len(match.group(1))
        before = text[: match.start()]
        if _BEFORE_UNDER_RE.search(before):
            return f"<={'$' * max(length - 1, 1)}"
        if _BEFORE_OVER_RE.search(before):
            return f">={'$' * min(length + 1, 4)}"
        return "$" * length
    price = cfg.get("price_band", {}) if isinstance(cfg.get("price_band"), dict) else {}
    token_set = set(tokens)
    if any(word in token_set for word in price.get("at_most_two", [])):
        return "<=$$"
    if any(word in token_set for word in price.get("at_least_three", [])):
        return ">=$$$"
    return None


def extract_filters(text: str, vocab: Vocabulary = DEFAULT) -> BrowseFilters:
    """The narrowing the guest gave (cuisine, price band, dietary need, category, party size), from
    vocabulary data. `dietary` is always the structured enum, never free text."""
    cfg = _filters_cfg(vocab)
    tokens = tokenize(text)

    cuisine = _first_match(cfg.get("cuisine", {}), tokens)
    dietary_key = _first_match(cfg.get("dietary", {}), tokens)
    category = _first_match(cfg.get("category", {}), tokens)
    party = _PARTY_RE.search(text)
    return BrowseFilters(
        cuisine=cuisine,
        price_band=_price_band(text, tokens, cfg),
        dietary=DietaryRestriction(dietary_key) if dietary_key else None,
        category=category,
        party_size=int(party.group(1)) if party else None,
    )


def wants_stay_period(text: str) -> bool:
    """True when the guest means "the dates of my stay" ("during my stay", "while I'm here", …)."""
    tokens = tokenize(text)
    return any(phrase_present(tokens, phrase) for phrase in _STAY_PERIOD_PHRASES)


def resolve_window(text: str, *, today: date, wants_stay_period: bool) -> DateWindow | None:
    """A window the guest's own words give (`stated`), or the `needs_dates` marker for "my stay" with no dates
    (the node upgrades it to `stay_period` when a stay is on record). `None` means no window words at all —
    the node then applies its documented default and says so (FR-018)."""
    stated = parse_date_range(text, today=today)
    if stated is not None:
        return DateWindow(start=stated[0], end=stated[1], source="stated")
    if wants_stay_period:
        return DateWindow(start=today, end=today, source="needs_dates")
    return None


PROPERTY_NAMES: dict[str, str] = {"MGV": "Meridian Grand", "MHB": "Meridian Harbor"}
"""Display name per verified `tenant_id` (data-model.md `TenantProfile`). Never derived from message text."""

_REGION_WORDS: dict[str, tuple[str, ...]] = {
    "MGV": ("nevada", "las vegas", "vegas", "nv"),
    "MHB": ("massachusetts", "boston", "ma"),
}
"""What a guest may write to mean "this property" or the other one. Matched as whole tokens/phrases (the
feature-010 tokenizer), so "ma" never matches inside another word; the `ma` of "ma'am" is skipped."""


def _mentions(tokens: list[str], words: tuple[str, ...]) -> bool:
    for word in words:
        if " " in word:
            if phrase_present(tokens, word):
                return True
        elif word in tokens:
            index = tokens.index(word)
            if word == "ma" and tokens[index + 1 : index + 2] == ["am"]:
                continue  # "ma'am"
            return True
    return False


def detect_other_property(text: str, tenant_id: str) -> str | None:
    """The other tenant's `property_name` when `text` names one of *its* region words and none of the guest's
    own; otherwise `None`. A hit only adds the "I can show {property}'s options only" sentence — it never
    changes which catalogue is read (FR-007)."""
    own_words = _REGION_WORDS.get(tenant_id)
    if own_words is None:
        return None
    tokens = tokenize(text)
    if _mentions(tokens, own_words):
        return None
    for other_id, other_words in _REGION_WORDS.items():
        if other_id != tenant_id and _mentions(tokens, other_words):
            return PROPERTY_NAMES[other_id]
    return None
