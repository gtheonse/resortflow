"""Gambling-distress detection (ADR-036).

`check_distress_signal` reuses `policy/_similarity.py`'s corpus/embedding
technique against a distress-specific corpus. No model judgment participates
in this decision (constitution, FR-004) — the ambiguous band still escalates
(FR-018): there is no third, "unsure, proceed normally" outcome.
"""

from __future__ import annotations

from policy._similarity import best_match
from policy.types import DistressCheck

CONFIDENT_THRESHOLD = 0.72
"""Similarity at or above this value is a confident match: `flagged=True`."""

AMBIGUOUS_THRESHOLD = 0.55
"""Similarity at or above this value (but below `CONFIDENT_THRESHOLD`) is the
ambiguous band: `ambiguous=True`, which routes to handoff exactly the same
as `flagged=True` (FR-018) — there is no "unsure, proceed normally" outcome."""

DISTRESS_CORPUS: dict[str, str] = {
    "cant_stop": "I can't stop gambling even though I want to",
    "ruining_finances": "gambling is ruining my finances and my life",
    "self_identified_problem": "I think I have a gambling problem",
    "want_to_talk": "I need to talk to someone about my gambling",
    "chasing_losses": "I keep chasing my losses and betting more to try to win it back",
    "hiding_losses": "I've been hiding how much money I've lost gambling from my family",
    "borrowed_money": "I borrowed money I can't repay to keep gambling",
    "cant_control_spending": "I can't control how much I'm betting anymore",
    "gambling_debt": "I'm in debt because of gambling and don't know what to do",
    "lost_everything": "I've lost everything to gambling",
}
"""Example distress utterances, deliberately phrased as first-person
statements (not questions) to stay distinct from `agents/gaming/nodes/
intent_parser.py`'s `_RG_KEYWORDS` (which classifies an informational
*question* about self-exclusion status into the `rg_status` lookup intent —
a different, lower-stakes classification than this module's)."""


def check_distress_signal(utterance: str) -> DistressCheck:
    """Deterministic corpus/embedding-similarity screen. Same technique
    `policy/injection.py` uses (ADR-036), against this module's own corpus
    and thresholds."""
    _, score = best_match(utterance, DISTRESS_CORPUS)
    if score >= CONFIDENT_THRESHOLD:
        return DistressCheck(flagged=True, ambiguous=False)
    if score >= AMBIGUOUS_THRESHOLD:
        return DistressCheck(flagged=False, ambiguous=True)
    return DistressCheck(flagged=False, ambiguous=False)
