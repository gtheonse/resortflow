"""Shared internal types returned by `policy/` functions.

Not `ResortFlowState` keys — internal to this package, returned by its functions
and consumed by call sites, which translate the relevant parts into a
`PolicyFlag` / `SagaEvent` / guest-facing message
(`specs/006-policy-guardrails-hitl/data-model.md`).
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from gateway.gaming.models import ResponsibleGamingStatus

from graph.models import DietaryRequirement


class GamingClearance(NamedTuple):
    """Result of `require_gaming_clear()`. Consumed by both call sites
    (gaming lookup, Offer Agent) so both branch on the same shape."""

    permitted: bool
    status: ResponsibleGamingStatus | None
    """`None` only when `failure_reason == "dependency_unavailable"`."""
    failure_reason: Literal["self_excluded", "dependency_unavailable"] | None


class DietaryFidelityResult(NamedTuple):
    """Result of the post-flight allergen check — distinct from
    `agents.dining.state.DietaryConflict` (menu-content matching, whose
    fields — `menu_item`, `chunk_id`, `source_uri` — describe a matched menu
    item and don't fit this check). `missing` is about
    conversation-vs-structured-payload fidelity specifically: a restriction
    the guest mentioned in conversation that never made it into the
    proposal's structured `dietary` field."""

    fidelity_ok: bool
    missing: list[DietaryRequirement]


class InjectionScreeningResult(NamedTuple):
    """Per-chunk verdict, produced before a retrieved chunk is added to a
    generation context."""

    chunk_id: str
    verdict: Literal["clean", "suspect"]
    matched_pattern_id: str | None
    similarity_score: float | None


class DistressCheck(NamedTuple):
    """Result of screening one guest utterance for gambling-related
    distress. `ambiguous=True` routes to handoff too (FR-018) — there is no
    third, "unsure, proceed normally" outcome."""

    flagged: bool
    ambiguous: bool
