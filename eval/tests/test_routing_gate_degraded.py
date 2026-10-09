"""Feature 010, T067 — the constitution's degraded-config rule: a
deliberately weakened router (typo tolerance disabled) must fail
`routing_accuracy` on holdout (US5 Acceptance Scenario 2)."""

from __future__ import annotations

import pytest

from eval.routing_scorer import load_routing_cases, score_routing_cases
from graph.routing.vocabulary import DEFAULT


@pytest.mark.asyncio
async def test_degraded_vocabulary_drops_routing_accuracy_below_floor():
    degraded = DEFAULT.model_copy(deep=True)
    # Disable typo tolerance entirely — every word must match exactly.
    degraded.tolerance.exact_max_len = 999
    degraded.tolerance.swap_only_len = 999
    degraded.tolerance.one_edit_min_len = 1000
    degraded.tolerance.two_edit_min_len = 1001
    # The `misspellings` exact-list (FR-002a) is a SEPARATE mechanism from
    # `within_tolerance` — disabling tolerance alone leaves every
    # known-misspelling entry ("restaurent", "tabel", "cancle", ...) still
    # matching, which is exactly why an earlier version of this fixture
    # under-degraded the router. A genuinely "no typo tolerance at all"
    # fixture clears both.
    for service_vocab in degraded.services.values():
        service_vocab.misspellings = []

    cases = load_routing_cases()
    baseline_scores, _ = await score_routing_cases(cases, split="holdout")
    scores, _not_evaluated = await score_routing_cases(cases, split="holdout", vocab=degraded)

    def _mean(all_scores, *, prefix: str | None = None) -> float:
        picked = [
            s.raw_values[0] for s in all_scores
            if s.metric_name == "routing_accuracy" and (prefix is None or s.conversation_id.startswith(prefix))
        ]
        return sum(picked) / len(picked)

    # Feature 011: the holdout set now also carries the browse categories, which dilutes an *overall* mean (the
    # degraded router still scored 0.965 overall) — a single mean can hide a whole category collapsing. The
    # degraded router must (a) lose accuracy against the healthy baseline and (b) fail the very categories
    # typo tolerance exists for, on their own.
    assert _mean(scores) < _mean(baseline_scores), "degrading typo tolerance must reduce overall accuracy"
    typo_accuracy = _mean(scores, prefix="typo-")
    assert typo_accuracy < 0.95, (
        f"degraded (no typo tolerance) accuracy on the typo category was {typo_accuracy:.3f} — "
        "expected it to fall below the 0.95 floor"
    )
