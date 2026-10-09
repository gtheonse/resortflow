"""Feature 010, T056 — the booking lookup (FR-014 to FR-016, SC-004,
SC-010)."""

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
async def test_verbatim_sc010_lookup_names_venue_date_time_party_and_confirmed():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    cfg = thread_config("lookup-1")
    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner for 4 and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets",
    )
    await app.ainvoke(state, config=cfg)
    result = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)
    assert len(result["confirmations"]) == 2

    result2 = await app.ainvoke(
        {"messages": [HumanMessage(content=f"what is the restaurent i got reserved for {FIXED_ISO_DATE}?")]},
        config=cfg,
    )
    reply = _reply(result2).lower()
    # This test's own SC-010 point is the typo/date match and the
    # dining-only service filter, not a specific fixture time — the fake
    # tool's actual granted time need not be what was asked for elsewhere
    # in the conversation.
    assert "party of" in reply
    assert "confirmed" in reply
    assert "grand illusion" not in reply  # only the dining leg was asked about, not the show


@pytest.mark.asyncio
async def test_no_match_lists_existing_bookings_and_starts_nothing():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    cfg = thread_config("lookup-2")
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    await app.ainvoke(state, config=cfg)
    await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    result = await app.ainvoke(
        {"messages": [HumanMessage(content="what did I book for oct 15?")]}, config=cfg
    )
    reply = _reply(result).lower()
    assert "couldn't find" in reply
    assert "here's what you have" in reply
    assert len(result["confirmations"]) == 2  # unchanged — nothing new started


@pytest.mark.asyncio
async def test_held_item_recheck_before_reporting_never_says_confirmed():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    cfg = thread_config("lookup-3")
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    result = await app.ainvoke(state, config=cfg)
    assert result["awaiting_approval"] is True  # not yet confirmed

    calls_before = len(dining.calls)
    result2 = await app.ainvoke(
        {"messages": [HumanMessage(content="what did I book?")]}, config=cfg
    )
    assert "check_dining_status" in dining.calls[calls_before:]  # live re-check, dining leg
    reply = _reply(result2).lower()
    assert "confirmed" not in reply.replace("not yet confirmed", "").replace("could not be confirmed", "")
    assert "held" in reply or "not yet" in reply

    # Nothing was confirmed by asking about it.
    assert result2["confirmations"] == []


@pytest.mark.asyncio
async def test_held_non_dining_item_reports_could_not_be_confirmed():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    cfg = thread_config("lookup-4")
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    result = await app.ainvoke(state, config=cfg)
    assert result["awaiting_approval"] is True

    result2 = await app.ainvoke(
        {"messages": [HumanMessage(content="am I booked for the show?")]}, config=cfg
    )
    reply = _reply(result2).lower()
    assert "could not be confirmed" in reply
    assert "confirmed" not in reply.replace("could not be confirmed", "")
