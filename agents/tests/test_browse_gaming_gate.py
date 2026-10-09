"""Feature 011, T044 — a gaming-only browse goes through the existing gated gaming graph (Principle IV, SC-010)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.gaming.tests.fakes import FAKE_FLOOR_GAMES, FakeGamingTools
from agents.gaming.tools import GamingToolError
from agents.tests.conftest import thread_config
from graph.models import Domain, RequestKind
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state

ASK = "what casino games do you have"


def _app(gaming: FakeGamingTools):
    return compile_supervisor_graph(
        checkpointer=MemorySaver(), tools_factory={Domain.GAMING: lambda t, g: gaming}, classifier=UnavailableClassifier()
    )


async def _run(gaming: FakeGamingTools, guest: str, name: str, text: str = ASK):
    app, cfg = _app(gaming), thread_config(name)
    nodes: list[str] = []
    async for update in app.astream(
        new_session_state(tenant_id="MGV", guest_id=guest, text=text), config=cfg, stream_mode="updates"
    ):
        nodes.extend(update.keys())
    state = (await app.aget_state(cfg)).values
    return nodes, state


def _ai(state) -> str:
    return "\n".join(str(m.content) for m in state["messages"] if m.type == "ai")


@pytest.mark.asyncio
async def test_a_cleared_guest_gets_the_list_via_the_gated_graph() -> None:
    gaming = FakeGamingTools()
    nodes, state = await _run(gaming, "guest-good-standing", "gg-ok")
    routed = [n for n in nodes if n in ("intent_router", "deterministic", "browse")]
    assert routed[:2] == ["intent_router", "deterministic"] and "browse" not in routed
    assert state["routing"].kind == RequestKind.BROWSE
    assert gaming.calls.index("check_rg_status") < gaming.calls.index("list_casino_floor")
    assert all(g.game_type in _ai(state).lower() for g in FAKE_FLOOR_GAMES[:1])


@pytest.mark.asyncio
async def test_a_self_excluded_guest_gets_only_the_mandated_message() -> None:
    gaming = FakeGamingTools()
    _nodes, state = await _run(gaming, "guest-self-excluded", "gg-excl")
    reply = _ai(state)
    assert "self-exclusion" in reply and "1-800-522-4700" in reply
    assert "list_casino_floor" not in gaming.calls
    assert "blackjack" not in reply.lower()


@pytest.mark.asyncio
async def test_an_unavailable_status_withholds_and_says_so() -> None:
    gaming = FakeGamingTools(fail_rg_status=GamingToolError("down", vendor_code="X", http_status=503))
    _nodes, state = await _run(gaming, "guest-good-standing", "gg-down")
    assert "temporarily unavailable" in _ai(state)
    assert "list_casino_floor" not in gaming.calls


@pytest.mark.asyncio
async def test_the_gate_is_rerun_every_turn() -> None:
    gaming = FakeGamingTools()
    app, cfg = _app(gaming), thread_config("gg-fresh")
    await app.ainvoke(new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text=ASK), config=cfg)
    first = gaming.calls.count("check_rg_status")
    gaming.rg_statuses = {"guest-good-standing": FakeGamingTools().rg_statuses["guest-self-excluded"]}
    second = await app.ainvoke({"messages": [HumanMessage(content=ASK)]}, config=cfg)
    assert gaming.calls.count("check_rg_status") > first
    assert "1-800-522-4700" in str(second["messages"][-1].content) or "self-exclusion" in _ai(second)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["place a bet on blackjack", "let me play blackjack", "book a table at the casino"])
async def test_wagering_and_transacting_never_become_a_browse_and_stay_read_only(text: str) -> None:
    gaming = FakeGamingTools()
    _nodes, state = await _run(gaming, "guest-good-standing", f"gg-{abs(hash(text))}", text)
    routing = state.get("routing")
    assert routing is None or routing.kind != RequestKind.BROWSE  # a wager/transaction is never a browse
    assert set(gaming.calls) <= {"check_rg_status", "list_casino_floor", "get_loyalty_status"}  # read-only domain
