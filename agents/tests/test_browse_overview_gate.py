"""Feature 011, T032a — the overview's gaming line is gated fresh every turn and fails closed (FR-015a, SC-010).

Must fail before T033."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FakeDiningTools
from agents.gaming.tests.fakes import FakeGamingTools
from agents.gaming.tools import GamingToolError
from agents.tests.conftest import thread_config
from graph.models import Domain
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state


def _app(gaming: FakeGamingTools):
    factory = {Domain.DINING: lambda t, g: FakeDiningTools(), Domain.GAMING: lambda t, g: gaming}
    return compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())


def _text(result) -> str:
    return next(m.content for m in reversed(result["messages"]) if "Here's what I can arrange" in str(m.content))


async def _overview(gaming: FakeGamingTools, guest: str, name: str) -> str:
    result = await _app(gaming).ainvoke(
        new_session_state(tenant_id="MGV", guest_id=guest, text="list the resort experiences"),
        config=thread_config(name),
    )
    return next(m.content for m in result["messages"] if "Here's what I can arrange" in str(m.content))


@pytest.mark.asyncio
async def test_a_cleared_guest_sees_the_casino_line() -> None:
    gaming = FakeGamingTools()
    reply = await _overview(gaming, "guest-good-standing", "ov-cleared")
    assert "casino" in reply.lower()
    assert "check_rg_status" in gaming.calls


@pytest.mark.asyncio
async def test_a_self_excluded_guest_sees_no_casino_text() -> None:
    reply = await _overview(FakeGamingTools(), "guest-self-excluded", "ov-excluded")
    assert "casino" not in reply.lower() and "gaming" not in reply.lower()
    assert "loyalty tier" in reply
    assert "Dining" in reply


@pytest.mark.asyncio
async def test_an_unavailable_status_fails_closed() -> None:
    gaming = FakeGamingTools(fail_rg_status=GamingToolError("down", vendor_code="X", http_status=503))
    reply = await _overview(gaming, "guest-good-standing", "ov-down")
    assert "casino" not in reply.lower()
    assert "Dining" in reply


@pytest.mark.asyncio
async def test_the_gate_runs_on_every_overview_turn() -> None:
    gaming = FakeGamingTools()
    app, cfg = _app(gaming), thread_config("ov-fresh")
    await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text="list the resort experiences"), config=cfg
    )
    before = gaming.calls.count("check_rg_status")
    assert before >= 1
    gaming.rg_statuses = {"guest-good-standing": FakeGamingTools().rg_statuses["guest-self-excluded"]}
    again = await app.ainvoke({"messages": [HumanMessage(content="list the resort experiences")]}, config=cfg)
    assert gaming.calls.count("check_rg_status") > before
    assert "casino" not in _text(again).lower()
