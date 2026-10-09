"""Feature 010, T071 — US4: `MODIFY_REQUEST` (FR-017, SC-009). This
closes the last branch of `contracts/graph-topology-v010.md`'s routing
table."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state


def _reply(result: dict) -> str:
    messages = result.get("messages") or []
    return getattr(messages[-1], "content", "") if messages else ""


@pytest.mark.asyncio
async def test_cancel_my_dinner_reservation_hands_off_without_new_booking():
    dining = FakeDiningTools()
    factory = {Domain.DINING: lambda t, g: dining}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(tenant_id="MGV", guest_id="g1", text="cancel my dinner reservation")
    cfg = thread_config("modify-1")
    result = await app.ainvoke(state, config=cfg)

    assert result["confirmations"] == []
    assert result["holds"] == []
    assert result.get("escalate") is True
    assert result.get("escalate_reason") == "modify_request"
    reply = _reply(result).lower()
    assert "team member" in reply


@pytest.mark.asyncio
async def test_move_my_spa_appointment_handled_the_same_way():
    entertainment = FakeEntertainmentTools()
    factory = {Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(tenant_id="MGV", guest_id="g1", text="move my spa appointment to 4pm")
    cfg = thread_config("modify-2")
    result = await app.ainvoke(state, config=cfg)

    assert result["confirmations"] == []
    assert result.get("escalate_reason") == "modify_request"


@pytest.mark.asyncio
async def test_make_it_6_people_after_a_booking_identifies_that_booking():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    cfg = thread_config("modify-3")
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner for 4 and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    await app.ainvoke(state, config=cfg)
    await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    result = await app.ainvoke({"messages": [HumanMessage(content="make it 6 people")]}, config=cfg)
    assert result.get("escalate_reason") == "modify_request"
    reply = _reply(result).lower()
    assert "meridian steakhouse" in reply or "team member" in reply


@pytest.mark.asyncio
async def test_next_message_after_modify_goes_to_human_handoff():
    dining = FakeDiningTools()
    factory = {Domain.DINING: lambda t, g: dining}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    cfg = thread_config("modify-4")
    state = new_session_state(tenant_id="MGV", guest_id="g1", text="cancel my dinner reservation")
    await app.ainvoke(state, config=cfg)

    result = await app.ainvoke({"messages": [HumanMessage(content="hello?")]}, config=cfg)
    reply = _reply(result).lower()
    assert "handed off" in reply or "team member" in reply
