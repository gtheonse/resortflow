"""T031 — subgraph-level tests against `FakeWellnessTools`. Each test
exercises the compiled graph end-to-end (no live sims/gateway required),
mirroring `agents.dining.tests.test_graph`'s style (`Command(resume=...)`
to cross an `interrupt()`, `MemorySaver` for resumable tests, uncheckpointed
`.ainvoke()` for single-shot ones)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.wellness.graph import compile_wellness_graph
from agents.wellness.nodes.intent_parser import RuleBasedIntentExtractor
from agents.wellness.state import WellnessState
from agents.wellness.tests.fakes import FakeWellnessTools
from agents.wellness.tools import WellnessToolError
from graph.ledger import NullHoldLedger
from graph.models import HoldStatus

TODAY = date(2026, 9, 11)


def make_extractor():
    class _Fixed(RuleBasedIntentExtractor):
        def extract(self, text, *, today):
            return super().extract(text, today=TODAY)

    return _Fixed()


def new_session_state(*, tenant_id: str, guest_id: str, session_id: str | None, text: str) -> WellnessState:
    """Seed a fresh `WellnessState` the way a real conversation entry would
    — mirrors `agents.dining.graph.new_session_state` (no standalone runner
    exists for wellness, so this test module seeds state directly)."""
    return WellnessState(
        tenant_id=tenant_id,
        guest_id=guest_id,
        session_id=session_id or uuid.uuid4().hex,
        jurisdiction="NV" if tenant_id == "MGV" else "MA",
        entitlements=[],
        saga_id=uuid.uuid4().hex,
        messages=[HumanMessage(content=text)],
        plan=None,
        replan_count=0,
        proposals=[],
        holds=[],
        confirmations=[],
        failures=[],
        retrieved=[],
        citations=[],
        awaiting_approval=False,
        approval_scope=None,
        policy_flags=[],
        escalate=False,
        saga_log=[],
        graph_version="test",
        clarification_count=0,
        escalate_reason=None,
    )


@pytest.mark.asyncio
async def test_approval_resume_confirms_after_hold():
    """(a) full happy path: discover -> availability -> hold -> approve ->
    confirm produces a `Confirmation`."""
    tools = FakeWellnessTools()
    checkpointer = MemorySaver()
    app = compile_wellness_graph(
        tools, checkpointer=checkpointer, intent_extractor=make_extractor(),
        ledger=NullHoldLedger(),
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", session_id=None, text="book a massage tomorrow"
    )
    config = {"configurable": {"thread_id": "happy-1"}}
    first = await app.ainvoke(state, config=config)
    assert first["awaiting_approval"] is True
    assert first["holds"][0].status == HoldStatus.HELD
    assert first["confirmations"] == []

    second = await app.ainvoke(Command(resume={"approved": True}), config=config)
    assert len(second["confirmations"]) == 1
    assert second["holds"][0].status == HoldStatus.CONFIRMED
    assert "hold_amenity_slot" in tools.calls and "confirm_amenity_booking" in tools.calls


@pytest.mark.asyncio
async def test_rejection_releases_hold_and_never_confirms():
    """(b) human_approval reject releases the hold and does NOT produce a
    Confirmation."""
    tools = FakeWellnessTools()
    checkpointer = MemorySaver()
    app = compile_wellness_graph(
        tools, checkpointer=checkpointer, intent_extractor=make_extractor(),
        ledger=NullHoldLedger(),
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", session_id=None, text="book a massage tomorrow"
    )
    config = {"configurable": {"thread_id": "reject-1"}}
    first = await app.ainvoke(state, config=config)
    assert first["awaiting_approval"] is True
    held_key = first["holds"][0].idempotency_key

    second = await app.ainvoke(Command(resume={"approved": False}), config=config)
    assert second["confirmations"] == []
    resolved = next(h for h in second["holds"] if h.idempotency_key == held_key)
    assert resolved.status == HoldStatus.RELEASED
    assert "release_amenity_hold" in tools.calls
    assert tools.released == [first["holds"][0].hold_id]


@pytest.mark.asyncio
async def test_no_availability_routes_to_end_with_no_hold_ever_placed():
    """(c) no-availability routes to END with no hold ever placed."""
    tools = FakeWellnessTools(available_times=[])
    app = compile_wellness_graph(tools, intent_extractor=make_extractor(), ledger=NullHoldLedger())
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", session_id=None, text="book a massage tomorrow"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "no-avail-1"}})

    assert result["holds"] == []
    assert result["confirmations"] == []
    assert "hold_amenity_slot" not in tools.calls
    assert "no availability" in result["messages"][-1].content.lower()


@pytest.mark.asyncio
async def test_zero_matching_services_ends_gracefully_with_suggestion_message():
    tools = FakeWellnessTools(services=[])
    app = compile_wellness_graph(tools, intent_extractor=make_extractor(), ledger=NullHoldLedger())
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", session_id=None, text="book a massage tomorrow"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "zero-1"}})

    assert result["proposals"] == []
    assert result["selected_proposal_id"] is None
    assert "broadening" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_hold_expired_at_confirm_time_escalates_requires_human():
    tools = FakeWellnessTools(hold_expired_on_revalidate=True)
    checkpointer = MemorySaver()
    app = compile_wellness_graph(
        tools, checkpointer=checkpointer, intent_extractor=make_extractor(),
        ledger=NullHoldLedger(),
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", session_id=None, text="book a massage tomorrow"
    )
    config = {"configurable": {"thread_id": "hold-expired-1"}}
    first = await app.ainvoke(state, config=config)
    assert first["awaiting_approval"] is True

    second = await app.ainvoke(Command(resume={"approved": True}), config=config)
    assert second["confirmations"] == []
    assert second["awaiting_approval"] is True
    assert second["approval_scope"].startswith("hold_expired:")
    assert second["failures"][-1].error_class.value == "REQUIRES_HUMAN"
    assert second["holds"][0].status == HoldStatus.EXPIRED


@pytest.mark.asyncio
async def test_hold_placement_failure_is_classified_and_never_confirms():
    tools = FakeWellnessTools(
        fail_hold=WellnessToolError("slot taken", vendor_code="SLOT_UNAVAILABLE", http_status=409)
    )
    app = compile_wellness_graph(tools, intent_extractor=make_extractor(), ledger=NullHoldLedger())
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", session_id=None, text="book a massage tomorrow"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "fail-hold-1"}})

    assert result["confirmations"] == []
    assert result["holds"][0].status == HoldStatus.RELEASED
    assert result["failures"][0].error_class.value == "TERMINAL"


def test_graph_compiles_with_no_orphan_nodes():
    tools = FakeWellnessTools()
    app = compile_wellness_graph(tools, intent_extractor=make_extractor(), ledger=NullHoldLedger())
    graph_repr = app.get_graph()
    node_names = set(graph_repr.nodes) - {"__start__", "__end__"}
    from agents.wellness.graph import ALL_NODE_NAMES

    assert node_names == ALL_NODE_NAMES
    sources = {edge.source for edge in graph_repr.edges}
    for name in ALL_NODE_NAMES - {"confirm"}:
        assert name in sources
