"""T046 (stay) — Intent parser: the one place free text becomes structured
stay intent. Mirrors `agents.dining.nodes.intent_parser`'s rule-based
backend shape (see that module's docstring for the full Azure/rule-based
fallback rationale) — like wellness, this module ships only the
deterministic `RuleBasedIntentExtractor`, since no Azure-backed stay
extractor was requested; `make_intent_parser_node` still takes an
injectable extractor so a future Azure backend can be added without
touching the graph.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Protocol

from agents.stay.state import StayIntentParsed, StayState
from graph.routing.temporal import parse_date as parse_temporal_date
from graph.routing.temporal import parse_explicit_range, parse_stay_length

# --- Room-class keyword table --------------------------------------------------

_ROOM_CLASS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "suite": ("suite",),
    "deluxe": ("deluxe",),
    "standard": ("standard",),
}

_PARTY_RE = re.compile(r"(?:table for|party of|for)\s+(\d{1,2})\b", re.IGNORECASE)
_NIGHTS_RE = re.compile(r"\bfor\s+(\d{1,2})\s+nights?\b", re.IGNORECASE)

DEFAULT_NIGHTS = 1
"""A stay always needs a departure date computed from `date_str + nights` —
default to a one-night stay rather than leaving `nights` ambiguous, mirroring
dining's own default-earliest-time convention (never blocking on a field
that has a reasonable, disclosed default)."""


def _extract_room_class(text: str) -> str | None:
    lowered = text.lower()
    for room_class, keywords in _ROOM_CLASS_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return room_class
    return None


def _extract_party_size(text: str) -> int | None:
    match = _PARTY_RE.search(text)
    return int(match.group(1)) if match else None


def _extract_date(text: str, *, today: date) -> str | None:
    """Arrival date, via `graph.routing.temporal.parse_date` (found live,
    demo report — see `agents.dining.nodes.intent_parser._extract_date`'s
    own docstring for the full root-cause writeup: this function used to
    understand only ISO dates and "tonight"/"today"/"tomorrow", so a
    multi-domain request naming any other date silently defaulted to
    tomorrow with no disclosure)."""
    parsed = parse_temporal_date(text, today=today)
    return parsed.isoformat() if parsed is not None else None


def _extract_nights(text: str) -> int:
    match = _NIGHTS_RE.search(text)
    if match:
        return int(match.group(1))
    # Feature 011 (T038): "next 5 days" / "for 5 days" / "a week" also state a length.
    return parse_stay_length(text) or DEFAULT_NIGHTS


class IntentExtractor(Protocol):
    def extract(self, text: str, *, today: date) -> StayIntentParsed: ...


class RuleBasedIntentExtractor:
    """Deterministic — no network call, no credentials required."""

    def extract(self, text: str, *, today: date) -> StayIntentParsed:
        room_class = _extract_room_class(text)
        date_str = _extract_date(text, today=today)
        nights = _extract_nights(text)
        # Found live (feature 011, scenario 9): "a room for oct 5 to oct 8" booked one night, because only the
        # arrival was read. An explicit two-date range states both ends, so it also fixes the night count.
        explicit = parse_explicit_range(text, today=today)
        if explicit is not None:
            date_str, nights = explicit[0].isoformat(), (explicit[1] - explicit[0]).days
        party_size = _extract_party_size(text)

        ambiguous: list[str] = []
        if date_str is None:
            ambiguous.append("date_str")
        if party_size is None:
            ambiguous.append("party_size")

        return StayIntentParsed(
            room_class=room_class,
            date_str=date_str,
            nights=nights,
            party_size=party_size,
            ambiguous_fields=ambiguous,
            raw_text=text,
        )


def build_default_extractor() -> IntentExtractor:
    """No Azure-backed stay extractor exists yet — deterministic fallback is
    the only path (mirrors `agents.wellness.nodes.intent_parser`'s
    `build_default_extractor` shape)."""
    return RuleBasedIntentExtractor()


def _latest_human_text(state: StayState) -> str:
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

    def intent_parser(state: StayState) -> dict[str, Any]:
        text = _latest_human_text(state)
        resolved_today = today or date.today()  # noqa: DTZ011 — local wall-clock date is correct here
        intent = _extractor.extract(text, today=resolved_today)
        return {"intent": intent}

    return intent_parser
