"""T020 [CRIT] — feature 006 (ADR-036, FR-004, FR-018): a gambling-distress
utterance routes to HUMAN_HANDOFF, on any topic, before the request ever
reaches a domain subgraph or the planner. Supervisor-level, matching
`test_gaming_refusal.py`'s pattern for a POLICY_PREFLIGHT-gated behavior."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FakeDiningTools
from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_distress_utterance_routes_to_human_handoff_not_planner():
    dining = FakeDiningTools()
    gaming = FakeGamingTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.GAMING: lambda t, g: gaming}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-good-standing",
        text="I can't stop gambling, it's ruining my finances and my life",
    )
    result = await app.ainvoke(state, config=thread_config("distress-1"))

    assert result["escalate"] is True
    assert result["escalate_reason"] == "distress_signal"
    assert result["proposals"] == []  # never reached PLANNER
    assert result["holds"] == []
    flags = result.get("policy_flags", [])
    assert any(f.rule == "distress_signal" and f.disposition == "escalated" for f in flags)
    content = result["messages"][-1].content
    assert "handed off to a team member" in content


@pytest.mark.asyncio
async def test_distress_routing_applies_regardless_of_topic():
    """FR-004: 'regardless of what else is being discussed' — a distress
    utterance framed around a dining request still escalates rather than
    booking anything."""
    dining = FakeDiningTools()
    factory = {Domain.DINING: lambda t, g: dining}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-good-standing",
        text="book me dinner but honestly I've lost everything to gambling and don't know what to do",
    )
    result = await app.ainvoke(state, config=thread_config("distress-2"))

    assert result["escalate"] is True
    assert result["escalate_reason"] == "distress_signal"
    assert result["confirmations"] == []


@pytest.mark.asyncio
async def test_ordinary_request_does_not_escalate():
    dining = FakeDiningTools()
    factory = {Domain.DINING: lambda t, g: dining}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-good-standing",
        text="what time does the casino floor open tonight",
    )
    result = await app.ainvoke(state, config=thread_config("no-distress-1"))

    assert result.get("escalate_reason") != "distress_signal"


@pytest.mark.asyncio
async def test_require_gaming_clear_reflects_a_status_change_between_turns():
    """FR-016: no caching across turns — a status change between turn N and
    turn N+1 is reflected on the very next call, not stale from the first.

    Passes an explicit, test-local `rg_statuses` dict rather than relying on
    the fake's shared module-level default — `FakeGamingTools` stores
    whatever dict it's given by reference, so mutating the default fixture
    in place would corrupt it for every other test in the session."""
    gaming = FakeGamingTools()  # __init__ copies its default fixture defensively
    factory = {Domain.GAMING: lambda t, g: gaming}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)
    cfg = thread_config("freshness-1")

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-good-standing", text="what's on the casino floor tonight"
    )
    await app.ainvoke(state, config=cfg)
    assert "list_casino_floor" in gaming.calls

    # Vendor now reports self-exclusion for the same guest — simulating a
    # status change between turns (e.g. the guest self-excluded mid-stay).
    # Mutating `gaming.rg_statuses` itself (the fake's own defensively-copied
    # dict), not the shared module-level fixture.
    gaming.rg_statuses["guest-good-standing"] = gaming.rg_statuses["guest-self-excluded"].model_copy(
        update={"guest_id": "guest-good-standing"}
    )
    state2 = new_session_state(
        tenant_id="MGV", guest_id="guest-good-standing", text="what's on the casino floor tonight"
    )
    second = await app.ainvoke(state2, config=thread_config("freshness-2"))
    content = second["messages"][-1].content
    assert "Nevada Council on Problem Gambling" in content
