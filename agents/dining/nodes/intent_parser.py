"""T046 — Intent parser: the one place free text becomes structured intent
and `list[graph.models.DietaryRequirement]` (AGENTS.md hard rule: never
carry a dietary restriction only in free text).

Two extraction backends:

- `AzureIntentExtractor` — Azure OpenAI (`MODEL_CHEAP` / gpt-4.1-mini per
  AGENTS.md), used when `AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_API_KEY` are
  configured.
- `RuleBasedIntentExtractor` — deterministic regex/keyword extraction, used
  when no Azure OpenAI credentials are configured in this environment
  (`.env` ships both blank; see `.env.example`). This is a disclosed
  deviation from the plan's "LLM node" framing, not a silent downgrade: the
  graph must stay testable and demoable without live credentials, and the
  node degrades to the rule-based path automatically and logs that it did
  so. Swapping in real credentials requires no code change — the node
  always tries Azure first.

Both backends produce the exact same two things: a local scratch
`IntentParsed` and a `list[DietaryRequirement]` — the dietary list is
merged into `IntentParsed["dietary"]` here and later copied onto the
`Proposal` by `venue_discovery`.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Protocol

from agents.dining.state import DiningState, IntentParsed
from graph.models import DietaryRequirement, DietaryRestriction, DietarySeverity
from graph.routing.temporal import parse_date as parse_temporal_date
from graph.routing.temporal import parse_time as parse_temporal_time
from observability.telemetry import start_generation_span
from prompts.registry import declare_required, resolve

# --- Prompt lineage (feature 009, ADR-052) -----------------------------------
#
# The extraction prompt now lives in `prompts/templates/dining_intent_extraction.yaml` and is resolved through
# `prompts.registry`. The two dietary vocabularies are POLICY, not wording (Principle IV): they are declared
# HERE, in code, as mandatory variables and rendered from the DietaryRestriction / DietarySeverity enumerations
# on every call, so neither a template edit nor a registry edit can remove them or change which restrictions
# exist (FR-007/FR-008). `validate_all()` and `agents/dining/tests/test_dietary_contract.py` check that the
# template still references them.

DINING_INTENT_PROMPT_ID = "dining_intent_extraction"
DINING_INTENT_REQUIRED_NAMES = ("dietary_restrictions", "dietary_severities")
declare_required(DINING_INTENT_PROMPT_ID, DINING_INTENT_REQUIRED_NAMES)


def dining_intent_required_vars() -> dict[str, str]:
    """The code-injected mandatory variables, rendered from the enumerations (never from a template or the
    registry). Rendered as `str(list)` so the prompt text is byte-identical to the previous f-string form."""
    return {
        "dietary_restrictions": str([r.value for r in DietaryRestriction]),
        "dietary_severities": str([s.value for s in DietarySeverity]),
    }


# --- Dietary keyword table ----------------------------------------------------

RESTRICTION_KEYWORDS: dict[DietaryRestriction, tuple[str, ...]] = {
    DietaryRestriction.SHELLFISH: ("shellfish", "shrimp", "prawn", "crab", "lobster"),
    DietaryRestriction.TREE_NUT: ("tree nut", "tree-nut", "almond", "walnut", "cashew", "pecan"),
    DietaryRestriction.PEANUT: ("peanut", "peanuts"),
    DietaryRestriction.DAIRY: ("dairy", "lactose", "milk"),
    DietaryRestriction.GLUTEN: ("gluten", "celiac", "coeliac", "wheat"),
    DietaryRestriction.EGG: ("egg", "eggs"),
    DietaryRestriction.SOY: ("soy", "soya"),
    DietaryRestriction.SESAME: ("sesame",),
    DietaryRestriction.FISH: ("fish allerg", "finned fish", " fish "),
    DietaryRestriction.VEGETARIAN: ("vegetarian",),
    DietaryRestriction.VEGAN: ("vegan",),
    DietaryRestriction.HALAL: ("halal",),
    DietaryRestriction.KOSHER: ("kosher",),
    DietaryRestriction.LOW_SODIUM: ("low sodium", "low-sodium", "low salt"),
}

RESTRICTION_KEYWORDS = {
    restriction: (*keywords, restriction.value)
    for restriction, keywords in RESTRICTION_KEYWORDS.items()
}
"""Every restriction's raw enum value (e.g. ``"tree_nut"``, ``"low_sodium"``)
is appended to its keyword tuple. Content's structured `allergens:`/
`dietary_tags:` YAML lists (content/chunker.py's `_fmt_allergens`) render as
the literal enum value joined into prose ("Allergens: tree_nut, peanut."),
which the hand-written space/hyphen keyword variants above (`"tree nut"`,
`"low sodium"`) do not substring-match — found when wiring `allergen_check`
against Track D's real corpus instead of the hand-crafted stub menu text,
which happened to only use space-separated phrasing. Without this, a
structured `tree_nut`/`low_sodium` tag would silently fail to flag a
severe allergy — exactly the silent-failure AGENTS.md warns against."""

_SEVERE_MARKERS = ("severe", "anaphyla", "life-threatening", "life threatening", "epipen")
_MODERATE_MARKERS = ("allerg",)  # "allergic to X" / "X allergy" without a severe marker
_INTOLERANCE_MARKERS = ("intoleran", "sensitivity", "sensitive to")

_CUISINE_KEYWORDS = (
    "steakhouse", "japanese", "sushi", "mexican", "vegetarian", "asian_fusion", "asian fusion",
    "italian", "seafood", "french", "chinese", "thai", "indian", "steak", "noodle",
)

_OCCASION_KEYWORDS = (
    "birthday", "anniversary", "proposal", "engagement", "honeymoon",
    "graduation", "bachelor", "bachelorette", "business dinner",
)

_PARTY_RE = re.compile(r"(?:table for|party of|for)\s+(\d{1,2})\b", re.IGNORECASE)


def _extract_party_size(text: str) -> int | None:
    match = _PARTY_RE.search(text)
    return int(match.group(1)) if match else None


def _extract_date(text: str, *, today: date) -> str | None:
    """Delegates to `graph.routing.temporal.parse_date` (found live, demo
    report): this function's own regex used to understand only ISO dates
    and "tonight"/"today"/"tomorrow" — nothing like "oct 3" — so the
    multi-domain FanOut path (`graph/supervisor.py`'s `_run_*_propose_and_
    hold` runners, the only callers of this rule-based extractor) silently
    defaulted every non-trivial date phrase to tomorrow, with no
    disclosure to the guest. `graph.routing.temporal` already parses month-
    day/day-month/weekday forms deterministically (feature 010, <5ms, no
    model call) and is reused here rather than duplicating its patterns."""
    parsed = parse_temporal_date(text, today=today)
    return parsed.isoformat() if parsed is not None else None


def _extract_time(text: str) -> str | None:
    """Delegates to `graph.routing.temporal.parse_time` — see `_extract_date`
    above for why."""
    parsed = parse_temporal_time(text)
    return f"{parsed[0]:02d}:{parsed[1]:02d}" if parsed is not None else None


def _extract_cuisine(text: str) -> str | None:
    lowered = text.lower()
    for kw in _CUISINE_KEYWORDS:
        if kw in lowered:
            return kw.replace(" ", "_")
    return None


def _normalise_cuisine(value: object) -> str | None:
    """The vendor's cuisine vocabulary is lowercase with underscores (`asian_fusion`); the model returns free text
    ("Mexican", "asian fusion"), which would never match a venue."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower().replace(" ", "_")


def _extract_occasion(text: str) -> str | None:
    lowered = text.lower()
    for kw in _OCCASION_KEYWORDS:
        if kw in lowered:
            return kw
    return None


def _extract_dietary(text: str) -> list[DietaryRequirement]:
    lowered = f" {text.lower()} "
    found: list[DietaryRequirement] = []
    for restriction, keywords in RESTRICTION_KEYWORDS.items():
        if not any(kw in lowered for kw in keywords):
            continue
        # Find a nearby severity marker; default to PREFERENCE for the
        # lifestyle/religious restrictions, ALLERGY_MODERATE for the
        # allergen ones when no marker at all is present (safer default —
        # AGENTS.md: never treat an unqualified allergen mention as merely
        # a preference).
        is_lifestyle = restriction in (
            DietaryRestriction.VEGETARIAN,
            DietaryRestriction.VEGAN,
            DietaryRestriction.HALAL,
            DietaryRestriction.KOSHER,
            DietaryRestriction.LOW_SODIUM,
        )
        if any(m in lowered for m in _SEVERE_MARKERS):
            severity = DietarySeverity.ALLERGY_SEVERE
        elif any(m in lowered for m in _INTOLERANCE_MARKERS):
            severity = DietarySeverity.INTOLERANCE
        elif any(m in lowered for m in _MODERATE_MARKERS):
            severity = DietarySeverity.ALLERGY_MODERATE
        elif is_lifestyle:
            severity = DietarySeverity.PREFERENCE
        else:
            severity = DietarySeverity.ALLERGY_MODERATE
        found.append(
            DietaryRequirement(restriction=restriction, severity=severity, detail=text.strip()[:280])
        )
    return found


class IntentExtractor(Protocol):
    def extract(self, text: str, *, today: date) -> IntentParsed: ...


class RuleBasedIntentExtractor:
    """Deterministic fallback — no network call, no credentials required."""

    def extract(self, text: str, *, today: date) -> IntentParsed:
        party_size = _extract_party_size(text)
        date_str = _extract_date(text, today=today)
        time_str = _extract_time(text)
        cuisine = _extract_cuisine(text)
        occasion = _extract_occasion(text)
        dietary = _extract_dietary(text)

        ambiguous: list[str] = []
        if party_size is None:
            ambiguous.append("party_size")
        if date_str is None:
            ambiguous.append("date_str")

        return IntentParsed(
            venue_pref=None,
            cuisine=cuisine,
            party_size=party_size,
            date_str=date_str,
            time_str=time_str,
            occasion=occasion,
            dietary=dietary,
            ambiguous_fields=ambiguous,
            raw_text=text,
        )


class AzureIntentExtractor:
    """Structured extraction via Azure OpenAI (`MODEL_CHEAP`). Only
    constructed when credentials are present; a call failure degrades to
    `RuleBasedIntentExtractor` rather than raising (this node must never
    become a single point of failure for the whole vertical slice)."""

    def __init__(self, *, endpoint: str, api_key: str, api_version: str, deployment: str) -> None:
        from openai import AzureOpenAI  # imported lazily — optional dependency path

        self._client = AzureOpenAI(
            azure_endpoint=endpoint, api_key=api_key, api_version=api_version
        )
        self._deployment = deployment

    def extract(self, text: str, *, today: date) -> IntentParsed:
        resolved = resolve(
            DINING_INTENT_PROMPT_ID,
            required_vars=dining_intent_required_vars(),
            runtime_vars={"guest_message": text},
        )
        with start_generation_span(
            "dining.intent_extraction",
            prompt=resolved,
            model=self._deployment,
            node="intent_parser",
            domain="dining",
        ):
            response = self._client.chat.completions.create(
                model=self._deployment,
                messages=resolved.messages,
                # `temperature` and `response_format` come from the prompt's validated config (the only two
                # keys the allow-list permits); the model itself is chosen by code, never by a prompt.
                **resolved.config,
            )
        payload = json.loads(response.choices[0].message.content or "{}")
        dietary = [
            DietaryRequirement(
                restriction=DietaryRestriction(d["restriction"]),
                severity=DietarySeverity(d["severity"]),
                detail=d.get("detail"),
            )
            for d in payload.get("dietary", [])
        ]
        party_size = payload.get("party_size")
        # Dates and times are arithmetic, so code does them: `today` never reached the prompt, which made "tomorrow"
        # and "tonight" come back as null (and `availability` then silently booked TOMORROW). The deterministic parser
        # wins; the model's value is kept only for phrases the parser does not understand.
        date_str = _extract_date(text, today=today) or payload.get("date_str")
        time_str = _extract_time(text) or payload.get("time_str")
        ambiguous = [f for f, v in (("party_size", party_size), ("date_str", date_str)) if v is None]
        return IntentParsed(
            venue_pref=None,
            cuisine=_normalise_cuisine(payload.get("cuisine")),
            party_size=party_size,
            date_str=date_str,
            time_str=time_str,
            occasion=payload.get("occasion"),
            dietary=dietary,
            ambiguous_fields=ambiguous,
            raw_text=text,
        )


def build_default_extractor() -> IntentExtractor:
    """Azure OpenAI when configured, rule-based otherwise. This is the
    documented credential-fallback path (see module docstring)."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    if endpoint and api_key:
        try:
            return AzureIntentExtractor(
                endpoint=endpoint,
                api_key=api_key,
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                deployment=os.environ.get("MODEL_CHEAP", "gpt-4.1-mini"),
            )
        except Exception:  # noqa: BLE001 — optional-dependency/misconfig must degrade, never crash
            # Never let a broken optional dependency/misconfiguration take
            # down the vertical slice — fall back deterministically.
            return RuleBasedIntentExtractor()
    return RuleBasedIntentExtractor()


def _latest_human_text(state: DiningState) -> str:
    messages = state.get("messages") or []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        msg_type = getattr(message, "type", None)
        if content and msg_type in (None, "human"):
            return content if isinstance(content, str) else str(content)
    return ""


def make_intent_parser_node(extractor: IntentExtractor | None = None, *, today: date | None = None):
    """Factory so tests/eval can inject a fixed extractor and a fixed
    `today` (dates like "tonight"/"tomorrow" must resolve deterministically
    in tests)."""

    _extractor = extractor or build_default_extractor()

    def intent_parser(state: DiningState) -> dict:
        text = _latest_human_text(state)
        resolved_today = today or date.today()  # noqa: DTZ011 — local wall-clock date is correct here
        intent = _extractor.extract(text, today=resolved_today)
        return {"intent": intent}

    return intent_parser
