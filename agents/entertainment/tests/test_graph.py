"""T027 — subgraph-level tests against `FakeEntertainmentTools`. Each test
exercises the compiled graph end-to-end (no live sims/gateway required),
mirroring `agents/dining/tests/test_graph.py`'s style — this is what
actually proves the node wiring, reducers, and conditional edges agree with
each other."""

from __future__ import annotations

from datetime import date

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.entertainment.graph import compile_entertainment_graph, new_session_state
from agents.entertainment.nodes.intent_parser import RuleBasedIntentExtractor
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from graph.ledger import NullHoldLedger
from graph.models import HoldStatus

TODAY = date(2026, 9, 11)


def make_extractor():
    class _Fixed(RuleBasedIntentExtractor):
        def extract(self, text, *, today):
            return super().extract(text, today=TODAY)

    return _Fixed()


@pytest.mark.asyncio
async def test_happy_path_approve_produces_confirmation():
    tools = FakeEntertainmentTools()
    checkpointer = MemorySaver()
    app = compile_entertainment_graph(
        tools,
        checkpointer=checkpointer,
        intent_extractor=make_extractor(),
        ledger=NullHoldLedger(),
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="2 tickets for the concert tomorrow"
    )
    config = {"configurable": {"thread_id": "happy-1"}}

    first = await app.ainvoke(state, config=config)
    assert first["awaiting_approval"] is False  # not set for entertainment's unconditional gate
    assert first["__interrupt__"]
    assert first["holds"][0].status == HoldStatus.HELD
    assert first["confirmations"] == []

    second = await app.ainvoke(Command(resume={"approved": True}), config=config)
    assert len(second["confirmations"]) == 1
    assert second["holds"][0].status == HoldStatus.CONFIRMED
    assert "hold_seats" in tools.calls
    assert "purchase_tickets" in tools.calls
    assert "get_order_status" in tools.calls


@pytest.mark.asyncio
async def test_human_approval_reject_releases_hold_and_never_purchases():
    tools = FakeEntertainmentTools()
    checkpointer = MemorySaver()
    app = compile_entertainment_graph(
        tools,
        checkpointer=checkpointer,
        intent_extractor=make_extractor(),
        ledger=NullHoldLedger(),
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="2 tickets for the concert tomorrow"
    )
    config = {"configurable": {"thread_id": "reject-1"}}

    first = await app.ainvoke(state, config=config)
    assert first["__interrupt__"]
    held_key = first["holds"][0].idempotency_key

    second = await app.ainvoke(Command(resume={"approved": False}), config=config)
    assert second["confirmations"] == []
    resolved = next(h for h in second["holds"] if h.idempotency_key == held_key)
    assert resolved.status == HoldStatus.RELEASED
    assert "release_seat_hold" in tools.calls
    assert "purchase_tickets" not in tools.calls


@pytest.mark.asyncio
async def test_no_seats_available_routes_to_end_without_ever_holding():
    tools = FakeEntertainmentTools(section_available=0)
    app = compile_entertainment_graph(
        tools, intent_extractor=make_extractor(), ledger=NullHoldLedger()
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="2 tickets for the concert tomorrow"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "no-seats-1"}})

    assert result["holds"] == []
    assert result["confirmations"] == []
    assert "hold_seats" not in tools.calls
    assert "no availability" in result["messages"][-1].content.lower()


@pytest.mark.asyncio
async def test_hold_placement_failure_is_classified_and_never_purchases():
    """eval/saga_suite.py's entertainment/hold cell (T032 matrix audit) —
    mirrors `agents/wellness/tests/test_graph.py`'s and
    `agents/dining/tests/test_graph.py`'s hold-placement-failure tests: the
    vendor rejects `hold_seats` itself (section sold out between inventory
    check and hold)."""
    from agents.entertainment.tools import EntertainmentToolError

    tools = FakeEntertainmentTools(
        fail_hold=EntertainmentToolError("section sold out", vendor_code="SLOT_UNAVAILABLE", http_status=409)
    )
    app = compile_entertainment_graph(tools, intent_extractor=make_extractor(), ledger=NullHoldLedger())
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="2 tickets for the concert tomorrow"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "fail-hold-1"}})

    assert result["confirmations"] == []
    assert result["holds"][0].status == HoldStatus.RELEASED
    assert result["failures"][0].error_class.value == "TERMINAL"
    assert "purchase_tickets" not in tools.calls


@pytest.mark.asyncio
async def test_purchase_failure_is_classified_and_hold_expires_without_confirming():
    """eval/saga_suite.py's entertainment/confirm cell (T032 matrix audit) —
    the vendor rejects `purchase_tickets` after a successful hold; mirrors
    dining/stay's `fail_confirm` tests but at the single-domain subgraph
    level (entertainment always requires human approval before purchase, so
    this exercises `confirm`'s own `_expire`-and-escalate path directly,
    the same primitive `agents/tests/test_partial_failure_recovery.py`'s
    multi-domain saga test relies on to trigger `Recovery`)."""
    from agents.entertainment.tools import EntertainmentToolError

    tools = FakeEntertainmentTools(
        fail_purchase=EntertainmentToolError("card declined", vendor_code="PAYMENT_DECLINED", http_status=402)
    )
    checkpointer = MemorySaver()
    app = compile_entertainment_graph(
        tools, checkpointer=checkpointer, intent_extractor=make_extractor(), ledger=NullHoldLedger()
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="2 tickets for the concert tomorrow"
    )
    config = {"configurable": {"thread_id": "fail-purchase-1"}}

    first = await app.ainvoke(state, config=config)
    assert first["__interrupt__"]

    second = await app.ainvoke(Command(resume={"approved": True}), config=config)
    assert second["confirmations"] == []
    assert second["holds"][0].status in (HoldStatus.EXPIRED, HoldStatus.RELEASED)
    assert second["failures"]
    assert "purchase_tickets" in tools.calls


def test_graph_compiles_with_no_orphan_nodes():
    tools = FakeEntertainmentTools()
    app = compile_entertainment_graph(tools, intent_extractor=make_extractor())
    graph_repr = app.get_graph()
    node_names = set(graph_repr.nodes) - {"__start__", "__end__"}
    from agents.entertainment.graph import ALL_NODE_NAMES

    assert node_names == ALL_NODE_NAMES
    sources = {edge.source for edge in graph_repr.edges}
    for name in ALL_NODE_NAMES - {"purchase"}:
        assert name in sources


@pytest.mark.asyncio
async def test_zero_matching_events_ends_gracefully_with_suggestion_message():
    tools = FakeEntertainmentTools(events=[])
    app = compile_entertainment_graph(tools, intent_extractor=make_extractor())
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="2 tickets for the concert tomorrow"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "zero-1"}})

    assert result["proposals"] == []
    assert result["selected_proposal_id"] is None
    assert "broadening" in result["messages"][-1].content
