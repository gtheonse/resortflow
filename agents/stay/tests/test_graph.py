"""Subgraph-level tests against `FakeStayTools` — mirrors
`agents/dining/tests/test_graph.py`'s style: exercise the compiled graph
end-to-end (no live sims/gateway required)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.stay.graph import compile_stay_graph
from agents.stay.nodes.intent_parser import RuleBasedIntentExtractor
from agents.stay.state import StayState
from agents.stay.tests.fakes import FakeStayTools
from agents.stay.tools import StayToolError
from graph.models import HoldStatus

TODAY = date(2026, 9, 11)


def make_extractor():
    class _Fixed(RuleBasedIntentExtractor):
        def extract(self, text, *, today):
            return super().extract(text, today=TODAY)

    return _Fixed()


def new_session_state(*, tenant_id: str, guest_id: str, text: str) -> StayState:
    return StayState(
        tenant_id=tenant_id, guest_id=guest_id, session_id=uuid.uuid4().hex,
        jurisdiction="NV" if tenant_id == "MGV" else "MA", entitlements=[],
        saga_id=uuid.uuid4().hex, messages=[HumanMessage(content=text)],
        plan=None, replan_count=0, proposals=[], holds=[], confirmations=[],
        failures=[], retrieved=[], citations=[], awaiting_approval=False,
        approval_scope=None, policy_flags=[], escalate=False, saga_log=[],
        graph_version="5.0", clarification_count=0, escalate_reason=None,
    )


@pytest.mark.asyncio
async def test_happy_path_produces_confirmation():
    tools = FakeStayTools()
    app = compile_stay_graph(tools, intent_extractor=make_extractor(), checkpointer=MemorySaver())
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="a standard room for 2 tomorrow for 2 nights"
    )
    cfg = {"configurable": {"thread_id": "stay-happy-1"}}
    result = await app.ainvoke(state, config=cfg)

    assert result["awaiting_approval"] is True
    result2 = await app.ainvoke(Command(resume={"approved": True}), config=cfg)

    assert len(result2["confirmations"]) == 1
    assert result2["holds"][0].status == HoldStatus.CONFIRMED
    assert "hold_room" in tools.calls and "confirm_room_reservation" in tools.calls


@pytest.mark.asyncio
async def test_reject_releases_hold_without_confirming():
    tools = FakeStayTools()
    app = compile_stay_graph(tools, intent_extractor=make_extractor(), checkpointer=MemorySaver())
    state = new_session_state(tenant_id="MGV", guest_id="g1", text="a deluxe room for 2 tomorrow")
    cfg = {"configurable": {"thread_id": "stay-reject-1"}}
    result = await app.ainvoke(state, config=cfg)
    assert result["awaiting_approval"] is True

    result2 = await app.ainvoke(Command(resume={"approved": False}), config=cfg)

    assert result2["confirmations"] == []
    assert result2["holds"][0].status == HoldStatus.RELEASED
    assert tools.released
    assert "confirm_room_reservation" not in tools.calls


@pytest.mark.asyncio
async def test_no_rooms_available_ends_without_a_hold():
    tools = FakeStayTools(offers=[])
    app = compile_stay_graph(tools, intent_extractor=make_extractor(), checkpointer=MemorySaver())
    state = new_session_state(tenant_id="MGV", guest_id="g1", text="a room for 2 tomorrow")
    cfg = {"configurable": {"thread_id": "stay-none-1"}}
    result = await app.ainvoke(state, config=cfg)

    assert result["proposals"] == []
    assert result["holds"] == []
    assert "hold_room" not in tools.calls


@pytest.mark.asyncio
async def test_hold_placement_failure_is_classified_and_never_confirms():
    """eval/saga_suite.py's stay/hold cell (T032 matrix audit) — mirrors
    `agents/wellness/tests/test_graph.py`'s hold-placement-failure test and
    `agents/dining/tests/test_graph.py`'s equivalent: the vendor rejects
    `hold_room` itself, distinct from `test_no_rooms_available_ends_without_a_hold`
    (nothing to hold at all) and from the confirm-time revalidation failure
    below (an already-placed hold expiring before confirm)."""
    tools = FakeStayTools(
        fail_hold=StayToolError("room taken", vendor_code="SLOT_UNAVAILABLE", http_status=409)
    )
    app = compile_stay_graph(tools, intent_extractor=make_extractor(), checkpointer=MemorySaver())
    state = new_session_state(tenant_id="MGV", guest_id="g1", text="a standard room for 2 tomorrow")
    cfg = {"configurable": {"thread_id": "stay-fail-hold-1"}}
    result = await app.ainvoke(state, config=cfg)

    assert result["confirmations"] == []
    assert result["holds"][0].status == HoldStatus.RELEASED
    assert result["failures"][0].error_class.value == "TERMINAL"
    assert "confirm_room_reservation" not in tools.calls


@pytest.mark.asyncio
async def test_hold_expired_on_confirm_revalidation_escalates_to_approval():
    """No dedicated status-check tool exists for stay — `confirm` revalidates
    by re-calling `search_rooms`; a room that disappears from the results
    (e.g. sold out) must not be silently confirmed anyway."""
    tools = FakeStayTools()
    app = compile_stay_graph(tools, intent_extractor=make_extractor(), checkpointer=MemorySaver())
    state = new_session_state(tenant_id="MGV", guest_id="g1", text="a standard room for 2 tomorrow")
    cfg = {"configurable": {"thread_id": "stay-expire-1"}}
    await app.ainvoke(state, config=cfg)

    # Simulate the offer selling out entirely before confirm re-validates.
    tools.offers = []

    result2 = await app.ainvoke(Command(resume={"approved": True}), config=cfg)

    assert result2["confirmations"] == []
    assert result2["holds"][0].status == HoldStatus.EXPIRED
    assert result2["failures"]
    assert "confirm_room_reservation" not in tools.calls
