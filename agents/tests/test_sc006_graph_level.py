"""Feature 010 — SC-006 at the full-graph level (referenced from
`graph/routing/tests/test_classifier_failures.py`): with the tier-2
classifier unavailable, a turn through the *compiled supervisor graph*
still completes — reaching the existing clarifying question via
`CLARIFY`'s `interrupt()` — rather than raising. The router-level version
of this guarantee (`route()` itself never raises) is covered directly in
`graph/routing/tests/test_classifier_failures.py`; this test additionally
proves the wiring (`INTENT_ROUTER` -> `_route_from_intent_router` ->
`CLARIFY`) carries that guarantee all the way through a real turn."""

from __future__ import annotations

import pytest

from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_low_confidence_turn_completes_at_clarify_with_classifier_unavailable() -> None:
    graph = compile_supervisor_graph(classifier=UnavailableClassifier())
    state = new_session_state(
        tenant_id="MGV", guest_id="guest-1", text="flooble the wobblesnort arrangement please"
    )
    config = {"configurable": {"thread_id": "sc006-test"}}
    result = await graph.ainvoke(state, config=config)
    assert "__interrupt__" in result
    interrupt_value = result["__interrupt__"][0].value
    assert "question" in interrupt_value
