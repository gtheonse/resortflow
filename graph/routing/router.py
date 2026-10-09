"""Feature 010, T041 — `route()`: orchestrates tiers 0 -> 1 -> 2 into one
`RoutingDecision` (`specs/010-tiered-intent-routing/contracts/router-api.md`).
"""

from __future__ import annotations

from datetime import date

from graph.models import Domain, RequestKind, RoutingDecision, RoutingTier, Tier2Outcome
from graph.routing.browse import extract_filters, resolve_window, wants_stay_period
from graph.routing.classifier import ClassifierOutput, RoutingClassifier
from graph.routing.kinds import PreviousTurn, classify_kind
from graph.routing.matcher import match_services
from graph.routing.vocabulary import DEFAULT, Vocabulary

HIGH_CONFIDENCE = 0.7
"""FR-009's confidence gate — matches `graph.models.HIGH_CONFIDENCE`;
re-declared here as the router-api contract's own constant so callers of
this module don't need a `graph.models` import just for this threshold."""


async def route(
    text: str,
    *,
    previous: PreviousTurn | None,
    classifier: RoutingClassifier,
    vocab: Vocabulary = DEFAULT,
    today: date | None = None,
) -> RoutingDecision:
    """Tiers 0-1 first (`match_services` + `classify_kind`, both pure and
    synchronous); the classifier is awaited only when the tier 0-1
    decision is below `HIGH_CONFIDENCE` and is neither `smalltalk` nor
    `out_of_scope` (FR-009/FR-019 — a decline or a brief reply must never
    depend on a model call). Never raises: any classifier failure becomes
    an undecided decision, which routes to `CLARIFY`."""
    services = match_services(text, vocab)
    decision = classify_kind(text, services, previous=previous, vocab=vocab, today=today)

    if decision.confidence >= HIGH_CONFIDENCE:
        return decision
    if decision.kind in (RequestKind.SMALLTALK, RequestKind.OUT_OF_SCOPE):
        return decision

    result = await classifier.classify(text, previous=previous)
    if result.outcome != Tier2Outcome.DECIDED or result.output is None:
        return RoutingDecision(
            kind=None, services=[], confidence=0.0, tier=RoutingTier.LANGUAGE_MODEL, tier2_outcome=result.outcome
        )

    output: ClassifierOutput = result.output
    if output.kind in (RequestKind.OUT_OF_SCOPE, RequestKind.SMALLTALK) and output.services:
        # The model returned a kind that must carry no services but did —
        # treat as invalid rather than silently dropping the services list
        # and pretending the model got it right.
        return RoutingDecision(
            kind=None, services=[], confidence=0.0, tier=RoutingTier.LANGUAGE_MODEL, tier2_outcome=Tier2Outcome.INVALID
        )
    if output.kind == RequestKind.MODIFY and Domain.GAMING in output.services:
        # FR-028: gaming is read-only by construction; a classifier output
        # that would pair MODIFY with gaming is rejected outright.
        return RoutingDecision(
            kind=None, services=[], confidence=0.0, tier=RoutingTier.LANGUAGE_MODEL, tier2_outcome=Tier2Outcome.INVALID
        )
    if output.kind == RequestKind.BROWSE:
        if Domain.GAMING in output.services and len(output.services) > 1:
            # A browse may include gaming only alone (the gated gaming-only path); anything else is not a
            # decision this router will act on.
            return RoutingDecision(
                kind=None, services=[], confidence=0.0, tier=RoutingTier.LANGUAGE_MODEL,
                tier2_outcome=Tier2Outcome.INVALID,
            )
        # The model only chooses the kind and services; filters and window are read deterministically from the
        # message so a tier-2 browse is bounded exactly like a tier-1 one.
        filters = extract_filters(text, vocab)
        return RoutingDecision(
            kind=RequestKind.BROWSE,
            services=output.services,
            tier=RoutingTier.LANGUAGE_MODEL,
            confidence=HIGH_CONFIDENCE,
            tier2_outcome=Tier2Outcome.DECIDED,
            browse_filters=None if filters.model_dump() == {k: None for k in filters.model_dump()} else filters,
            window=resolve_window(text, today=today or date.today(), wants_stay_period=wants_stay_period(text)),  # noqa: DTZ011 - local wall-clock date, as elsewhere
        )
    return RoutingDecision(
        kind=output.kind,
        services=output.services,
        tier=RoutingTier.LANGUAGE_MODEL,
        confidence=HIGH_CONFIDENCE,
        tier2_outcome=Tier2Outcome.DECIDED,
    )
