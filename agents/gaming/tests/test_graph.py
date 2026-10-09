"""Subgraph-level tests against `FakeGamingTools`. Also the structural
read-only-by-construction proof (d) — a simpler version of what T055's
supervisor-level test does more thoroughly."""

from __future__ import annotations

import inspect
import uuid

import pytest
from langchain_core.messages import HumanMessage

from agents.gaming.graph import compile_gaming_graph
from agents.gaming.state import GamingState
from agents.gaming.tests.fakes import FakeGamingTools
from agents.gaming.tools import GamingToolError, GamingTools

_MUTATION_VERBS = ("hold", "confirm", "purchase", "book", "reserve", "cancel", "place", "wager")


def new_session_state(*, tenant_id: str, guest_id: str, text: str) -> GamingState:
    return GamingState(
        tenant_id=tenant_id, guest_id=guest_id, session_id=uuid.uuid4().hex,
        jurisdiction="NV" if tenant_id == "MGV" else "MA", entitlements=[],
        saga_id=uuid.uuid4().hex, messages=[HumanMessage(content=text)],
        plan=None, replan_count=0, proposals=[], holds=[], confirmations=[],
        failures=[], retrieved=[], citations=[], awaiting_approval=False,
        approval_scope=None, policy_flags=[], escalate=False, saga_log=[],
        graph_version="5.0", clarification_count=0, escalate_reason=None,
    )


@pytest.mark.asyncio
async def test_floor_games_lookup_lists_games():
    tools = FakeGamingTools()
    app = compile_gaming_graph(tools)
    state = new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text="what's on the casino floor tonight")
    result = await app.ainvoke(state)

    assert "list_casino_floor" in tools.calls
    content = result["messages"][-1].content
    assert "blackjack" in content


@pytest.mark.asyncio
async def test_loyalty_status_lookup_reports_tier_and_points():
    tools = FakeGamingTools()
    app = compile_gaming_graph(tools)
    state = new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text="what's my loyalty tier and points balance")
    result = await app.ainvoke(state)

    assert "get_loyalty_status" in tools.calls
    content = result["messages"][-1].content
    assert "gold" in content and "12500" in content


@pytest.mark.asyncio
async def test_self_excluded_guest_never_sees_gaming_content():
    tools = FakeGamingTools()
    app = compile_gaming_graph(tools)
    state = new_session_state(
        tenant_id="MGV", guest_id="guest-self-excluded", text="what are my responsible gaming limits"
    )
    result = await app.ainvoke(state)

    assert "check_rg_status" in tools.calls
    assert "list_casino_floor" not in tools.calls
    assert "get_loyalty_status" not in tools.calls
    content = result["messages"][-1].content
    assert "Nevada Council on Problem Gambling" in content  # mandated_message surfaced verbatim
    assert "blackjack" not in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,expected_tool",
    [
        ("what's on the casino floor tonight", "list_casino_floor"),
        ("what's my loyalty tier and points balance", "get_loyalty_status"),
    ],
)
async def test_self_excluded_guest_never_sees_gaming_content_regardless_of_intent(
    text: str, expected_tool: str
):
    """Feature 006 (ADR-030): the concrete gap this feature closed —
    previously only the `rg_status` intent checked self-exclusion;
    `floor_games`/`loyalty_status` called their vendor tool directly with no
    check at all. Now `require_gaming_clear` runs before all three."""
    tools = FakeGamingTools()
    app = compile_gaming_graph(tools)
    state = new_session_state(tenant_id="MGV", guest_id="guest-self-excluded", text=text)
    result = await app.ainvoke(state)

    assert "check_rg_status" in tools.calls
    assert expected_tool not in tools.calls
    content = result["messages"][-1].content
    assert "Nevada Council on Problem Gambling" in content
    flags = result.get("policy_flags", [])
    assert any(f.rule == "self_exclusion_active" and f.disposition == "blocked" for f in flags)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,vendor_tool",
    [
        ("what are my responsible gaming limits", "check_rg_status"),
        ("what's on the casino floor tonight", "list_casino_floor"),
        ("what's my loyalty tier and points balance", "get_loyalty_status"),
    ],
)
async def test_dependency_unavailable_fails_closed_for_every_intent(text: str, vendor_tool: str):
    """FR-021: when the self-exclusion check's own dependency is
    unreachable, every gaming intent fails closed — not just `rg_status`."""
    tools = FakeGamingTools(
        fail_rg_status=GamingToolError("upstream timeout", http_status=503)
    )
    app = compile_gaming_graph(tools)
    state = new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text=text)
    result = await app.ainvoke(state)

    assert tools.calls == ["check_rg_status"]  # never reaches for a second tool
    content = result["messages"][-1].content
    assert "temporarily unavailable" in content
    flags = result.get("policy_flags", [])
    assert any(f.rule == "dependency_unavailable" and f.disposition == "blocked" for f in flags)
    assert any(f.rule == "dependency_unavailable" and f.disposition == "escalated" for f in flags)


def test_gaming_tools_protocol_has_no_mutating_method():
    """Structural proof, not a runtime check: `GamingTools` must never grow a
    method whose name suggests a mutation (Constitution Principle II —
    gaming stays read-only). Enforced by review discipline, verified here so
    a future addition fails CI loudly rather than silently."""
    method_names = [name for name, _ in inspect.getmembers(GamingTools, predicate=inspect.isfunction)]
    for name in method_names:
        assert not any(verb in name for verb in _MUTATION_VERBS), f"{name} looks mutating"
