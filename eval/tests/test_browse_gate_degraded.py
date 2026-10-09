"""Feature 011, T046 — degraded-config rule: with the browse vocabulary removed, holdout `browse_accuracy`
falls below its floor and the gate fails; the healthy router passes every blocking browse metric."""

from __future__ import annotations

import pytest

from eval.routing_scorer import load_routing_cases, score_routing_cases
from eval.runner import _METRIC_FLOORS
from graph.routing.vocabulary import DEFAULT


def _mean(scores, name: str) -> float:
    picked = [s.raw_values[0] for s in scores if s.metric_name == name]
    assert picked, f"no {name} scores — the metric is not wired"
    return sum(picked) / len(picked)


@pytest.mark.asyncio
async def test_healthy_router_meets_every_blocking_browse_floor_on_holdout() -> None:
    scores, _ = await score_routing_cases(load_routing_cases(), split="holdout")
    assert _mean(scores, "browse_accuracy") >= _METRIC_FLOORS["browse_accuracy"]
    assert _mean(scores, "browse_catalogue_misroute") <= _METRIC_FLOORS["browse_catalogue_misroute"]
    assert _mean(scores, "near_miss_browse_leak") <= _METRIC_FLOORS["near_miss_browse_leak"]
    assert _mean(scores, "stay_length_accuracy") >= _METRIC_FLOORS["stay_length_accuracy"]


@pytest.mark.asyncio
async def test_removing_the_browse_vocabulary_fails_the_browse_accuracy_floor() -> None:
    degraded = DEFAULT.model_copy(deep=True)
    degraded.kinds.pop("browse", None)
    scores, _ = await score_routing_cases(load_routing_cases(), split="holdout", vocab=degraded)
    assert _mean(scores, "browse_accuracy") < _METRIC_FLOORS["browse_accuracy"]
