"""T050 — Intent parser: the one place free text becomes structured gaming
intent (`agents.gaming.state.GamingIntentParsed`).

Only one backend exists here, unlike dining/wellness' Azure-first pattern:
all three gaming lookups are simple, closed-set classifications (which of
three read-only tools to call, plus an optional game-type filter) — exactly
the "ambiguity is bounded" case AGENTS.md's routing commitment says an LLM
should never be used for ("Never put an LLM where arithmetic will do").
`RuleBasedIntentExtractor` is therefore the only extractor this subgraph
needs, not a degraded fallback of a richer one.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from agents.gaming.state import GamingIntentKind, GamingIntentParsed
from graph.state import ResortFlowState

_RG_KEYWORDS = (
    "responsible gaming",
    "self-exclusion",
    "self exclusion",
    "self-excluded",
    "gambling problem",
    "limit",
    "cool off",
    "cool-off",
    "exclude myself",
)

_LOYALTY_KEYWORDS = ("loyalty", "points", "comp", "tier", "rewards", "player's club", "players club")

_FLOOR_KEYWORDS = (
    "table",
    "slots",
    "slot machine",
    "tournament",
    "blackjack",
    "poker",
    "floor",
    "roulette",
    "craps",
    "baccarat",
    "casino floor",
)

_GAME_TYPE_KEYWORDS: tuple[str, ...] = (
    "blackjack",
    "poker",
    "slots",
    "roulette",
    "craps",
    "baccarat",
)


def _classify_intent_kind(lowered: str) -> GamingIntentKind:
    if any(kw in lowered for kw in _RG_KEYWORDS):
        return "rg_status"
    if any(kw in lowered for kw in _LOYALTY_KEYWORDS):
        return "loyalty_status"
    if any(kw in lowered for kw in _FLOOR_KEYWORDS):
        return "floor_games"
    # Default: floor_games — the most general, least sensitive lookup when
    # intent is unclear. Never default to rg_status/loyalty_status, which
    # require a keyword match to be selected at all.
    return "floor_games"


def _extract_game_type(lowered: str) -> str | None:
    for kw in _GAME_TYPE_KEYWORDS:
        if kw in lowered:
            return kw
    return None


class IntentExtractor(Protocol):
    def extract(self, text: str, *, today: date) -> GamingIntentParsed: ...


class RuleBasedIntentExtractor:
    """Deterministic keyword classification — no network call, no
    credentials required. See module docstring for why this is the only
    extractor this subgraph has."""

    def extract(self, text: str, *, today: date) -> GamingIntentParsed:
        _ = today  # unused — gaming lookups aren't scheduled, kept for Protocol parity
        lowered = text.lower()
        intent_kind = _classify_intent_kind(lowered)
        game_type = _extract_game_type(lowered) if intent_kind == "floor_games" else None

        return GamingIntentParsed(
            intent_kind=intent_kind,
            game_type=game_type,
            ambiguous_fields=[],
            raw_text=text,
        )


def _latest_human_text(state: ResortFlowState) -> str:
    messages = state.get("messages") or []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        msg_type = getattr(message, "type", None)
        if content and msg_type in (None, "human"):
            return content if isinstance(content, str) else str(content)
    return ""


def make_intent_parser_node(extractor: IntentExtractor | None = None, *, today: date | None = None) -> Any:
    """Factory so tests/eval can inject a fixed `today` for parity with the
    other domains' factories, even though this extractor never consumes
    it."""

    _extractor = extractor or RuleBasedIntentExtractor()

    def intent_parser(state: ResortFlowState) -> dict[str, Any]:
        text = _latest_human_text(state)
        resolved_today = today or date.today()  # noqa: DTZ011 — local wall-clock date is correct here
        intent = _extractor.extract(text, today=resolved_today)
        return {"intent": intent}

    return intent_parser
