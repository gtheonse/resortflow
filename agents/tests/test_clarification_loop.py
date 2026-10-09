"""T071 — quickstart.md Scenario 5: an ambiguous request (no domain
resolvable) triggers a clarifying turn (FR-020, ADR-024); once the guest
answers with a resolvable domain, classification re-runs against the
combined input. Also covers the loop's own exhaustion path
(`MAX_CLARIFICATION_COUNT` -> `HumanHandoff`, `escalate_reason=
"clarification_bound"`). Filed here rather than under a user story because
FR-020/`Clarify` doesn't map to any of spec.md's five numbered stories."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.state import MAX_CLARIFICATION_COUNT
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_ambiguous_request_clarifies_then_resolves():
    dining = FakeDiningTools()
    factory = {Domain.DINING: lambda t, g: dining}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(tenant_id="MGV", guest_id="g1", text="book me a nice evening")
    cfg = thread_config("clarify-1")
    result = await app.ainvoke(state, config=cfg)

    # A node's own state update after its interrupt() call is only visible
    # once that call resolves on resume — not on the pass where it first
    # raises (LangGraph's interrupt semantics; see
    # graph.supervisor.make_deterministic_node's docstring for the same
    # property in the nested-subgraph case). __interrupt__ is therefore the
    # correct signal to check here, not clarification_count.
    assert result.get("__interrupt__"), "no domain resolvable -> must ask a clarifying question"

    result2 = await app.ainvoke(
        Command(resume=f"dinner on {FIXED_ISO_DATE} at 7pm for 2"), config=cfg
    )

    assert result2["clarification_count"] == 1
    # No dietary conflict in this request -> dining's own standalone graph
    # confirms directly without its own approval gate (that gate is
    # conditional on an allergen conflict, unlike every other domain's
    # unconditional post-hold approval) — the resolved classification
    # having reached DETERMINISTIC and completed at all is what this test
    # is proving, not a second interrupt.
    assert result2["plan"] is None  # single domain -> DETERMINISTIC, never PLANNER
    assert len(result2["confirmations"]) == 1
    assert result2["confirmations"][0].domain == Domain.DINING


@pytest.mark.asyncio
async def test_clarification_exhaustion_hands_off():
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory={})

    state = new_session_state(tenant_id="MGV", guest_id="g1", text="book me a nice evening")
    cfg = thread_config("clarify-2")
    result = await app.ainvoke(state, config=cfg)
    assert result.get("__interrupt__")

    for _ in range(MAX_CLARIFICATION_COUNT):
        result = await app.ainvoke(Command(resume="still not sure, surprise me"), config=cfg)

    assert result["escalate"] is True
    assert result["escalate_reason"] == "clarification_bound"
    assert not result.get("__interrupt__")
    assert result["clarification_count"] <= MAX_CLARIFICATION_COUNT + 1
