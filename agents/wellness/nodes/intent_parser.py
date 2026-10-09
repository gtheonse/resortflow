"""T029 — Intent parser: the one place free text becomes structured
wellness intent. Mirrors `agents.dining.nodes.intent_parser`'s rule-based
backend shape (see that module's docstring for the full Azure/rule-based
fallback rationale) — this module ships only the deterministic
`RuleBasedIntentExtractor`, since no Azure-backed wellness extractor was
requested; `make_intent_parser_node` still takes an injectable extractor so
a future Azure backend can be added without touching the graph.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Protocol

from agents.wellness.state import WellnessIntentParsed, WellnessState
from graph.routing.temporal import parse_date as parse_temporal_date
from graph.routing.temporal import parse_time as parse_temporal_time

# --- Category keyword table ---------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "spa": ("spa", "massage", "facial"),
    "fitness": ("gym", "fitness", "workout", "trainer"),
    "cabana": ("cabana", "pool"),
    "golf": ("golf", "tee time"),
}

_PARTY_RE = re.compile(r"(?:table for|party of|for)\s+(\d{1,2})\b", re.IGNORECASE)
_GENDER_PREF_RE = re.compile(r"\b(male|female)\s+(?:practitioner|therapist|trainer)\b", re.IGNORECASE)


def _extract_category(text: str) -> str | None:
    lowered = text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return category
    return None


def _extract_party_size(text: str) -> int | None:
    match = _PARTY_RE.search(text)
    return int(match.group(1)) if match else None


def _extract_date(text: str, *, today: date) -> str | None:
    """Via `graph.routing.temporal.parse_date` — see
    `agents.dining.nodes.intent_parser._extract_date`'s docstring for the
    full root-cause writeup (found live, demo report)."""
    parsed = parse_temporal_date(text, today=today)
    return parsed.isoformat() if parsed is not None else None


def _extract_time(text: str) -> str | None:
    """Via `graph.routing.temporal.parse_time`."""
    parsed = parse_temporal_time(text)
    return f"{parsed[0]:02d}:{parsed[1]:02d}" if parsed is not None else None


def _extract_gender_preference(text: str) -> str | None:
    match = _GENDER_PREF_RE.search(text)
    return match.group(1).lower() if match else None


class IntentExtractor(Protocol):
    def extract(self, text: str, *, today: date) -> WellnessIntentParsed: ...


class RuleBasedIntentExtractor:
    """Deterministic — no network call, no credentials required."""

    def extract(self, text: str, *, today: date) -> WellnessIntentParsed:
        category = _extract_category(text)
        date_str = _extract_date(text, today=today)
        time_str = _extract_time(text)
        party_size = _extract_party_size(text)
        gender_pref = _extract_gender_preference(text)

        ambiguous: list[str] = []
        if category is None:
            ambiguous.append("category")
        if date_str is None:
            ambiguous.append("date_str")

        return WellnessIntentParsed(
            category=category,
            date_str=date_str,
            time_str=time_str,
            party_size=party_size,
            practitioner_gender_preference=gender_pref,
            ambiguous_fields=ambiguous,
            raw_text=text,
        )


def build_default_extractor() -> IntentExtractor:
    """No Azure-backed wellness extractor exists yet — deterministic
    fallback is the only path (mirrors `agents.dining.nodes.intent_parser`'s
    `build_default_extractor` shape without duplicating the Azure branch)."""
    return RuleBasedIntentExtractor()


def _latest_human_text(state: WellnessState) -> str:
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

    def intent_parser(state: WellnessState) -> dict[str, Any]:
        text = _latest_human_text(state)
        resolved_today = today or date.today()  # noqa: DTZ011 — local wall-clock date is correct here
        intent = _extractor.extract(text, today=resolved_today)
        return {"intent": intent}

    return intent_parser
