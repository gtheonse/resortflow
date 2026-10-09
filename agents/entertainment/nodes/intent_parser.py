"""T025 — Intent parser: the one place free text becomes structured
entertainment intent. Mirrors `agents/dining/nodes/intent_parser.py`'s
`RuleBasedIntentExtractor` pattern, adapted to entertainment's classification/
date/quantity extraction (no dietary-equivalent concept exists in this
domain).

Only the deterministic rule-based backend is built here — the Azure OpenAI
backend dining also carries is not mirrored, since nothing in this task
requires it and AGENTS.md's ambiguity-based-routing commitment already
treats the deterministic path as first-class, not merely a fallback.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Protocol

from agents.entertainment.state import EntertainmentIntentParsed, EntertainmentState
from graph.routing.temporal import parse_date as parse_temporal_date

# --- Classification keyword table --------------------------------------------

_CLASSIFICATION_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Dict keys are the literal `classification` values sims/ticketing's own
    # seed data actually uses (verified: SELECT DISTINCT classification FROM
    # events -> concert/theater/comedy/sports/residency/nightlife) -- "music"
    # was never one of them, so every "concert"-keyword match sent
    # `classification="music"` to a vendor that has no event classified that
    # way, silently returning zero results for the single most obvious
    # entertainment query ("book a concert"). Found live (feature 008) once
    # the three real MCP-layer bugs blocking this call entirely were fixed.
    "concert": ("concert", "music", "band", "dj", "singer", "tour"),
    "theater": ("theater", "theatre", "show", "play", "musical", "broadway"),
    "sports": ("sports", "game", "match", "boxing", "fight", "ufc"),
    "comedy": ("comedy", "comedian", "stand-up", "standup"),
}

_QUANTITY_RE = re.compile(r"(?:tickets? for|party of|for)\s+(\d{1,2})\b", re.IGNORECASE)
_QUANTITY_SUFFIX_RE = re.compile(r"\b(\d{1,2})\s+tickets?\b", re.IGNORECASE)


def _extract_classification(text: str) -> str | None:
    lowered = text.lower()
    for classification, keywords in _CLASSIFICATION_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return classification
    return None


def _extract_quantity(text: str) -> int | None:
    match = _QUANTITY_RE.search(text)
    if match:
        return int(match.group(1))
    match = _QUANTITY_SUFFIX_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def _extract_date(text: str, *, today: date) -> str | None:
    """Via `graph.routing.temporal.parse_date` — see
    `agents.dining.nodes.intent_parser._extract_date`'s docstring for the
    full root-cause writeup (found live, demo report)."""
    parsed = parse_temporal_date(text, today=today)
    return parsed.isoformat() if parsed is not None else None


class IntentExtractor(Protocol):
    def extract(self, text: str, *, today: date) -> EntertainmentIntentParsed: ...


class RuleBasedIntentExtractor:
    """Deterministic fallback — no network call, no credentials required."""

    def extract(self, text: str, *, today: date) -> EntertainmentIntentParsed:
        classification = _extract_classification(text)
        date_str = _extract_date(text, today=today)
        quantity = _extract_quantity(text)

        ambiguous: list[str] = []
        if classification is None:
            ambiguous.append("classification")
        if date_str is None:
            ambiguous.append("date_str")

        return EntertainmentIntentParsed(
            classification=classification,
            date_str=date_str,
            quantity=quantity,
            section_pref=None,
            ambiguous_fields=ambiguous,
            raw_text=text,
        )


def build_default_extractor() -> IntentExtractor:
    """Rule-based only — see module docstring for why the Azure backend is
    not mirrored here."""
    return RuleBasedIntentExtractor()


def _latest_human_text(state: EntertainmentState) -> str:
    messages = state.get("messages") or []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        msg_type = getattr(message, "type", None)
        if content and msg_type in (None, "human"):
            return content if isinstance(content, str) else str(content)
    return ""


def make_intent_parser_node(extractor: IntentExtractor | None = None, *, today: date | None = None) -> Any:
    """Factory so tests/eval can inject a fixed extractor and a fixed
    `today` (dates like "tonight"/"tomorrow" must resolve deterministically
    in tests)."""

    _extractor = extractor or build_default_extractor()

    def intent_parser(state: EntertainmentState) -> dict[str, Any]:
        text = _latest_human_text(state)
        resolved_today = today or date.today()  # noqa: DTZ011 — local wall-clock date is correct here
        intent = _extractor.extract(text, today=resolved_today)
        return {"intent": intent}

    return intent_parser
