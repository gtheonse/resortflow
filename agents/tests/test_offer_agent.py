"""T045/T046 — `OFFER_AGENT` (ADR-030, ADR-037, FR-002, FR-015, SC-007).

T045 is a structural test, mirroring `agents/gaming/tests/test_graph.py`'s
own "no mutation verb" style: `make_offer_agent_node`'s body is inspected
directly to confirm `get_loyalty_status` is the only `GamingTools` method it
ever calls — the guarantee is the *absence* of a second call, not a filter
applied to what `get_loyalty_status` happens to return.
"""

from __future__ import annotations

import inspect

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.models import Domain
from graph.supervisor import (
    _apply_offer_agent,
    compile_supervisor_graph,
    new_session_state,
)


def test_offer_agent_calls_no_gaming_tool_other_than_get_loyalty_status() -> None:
    """`make_offer_agent_node` is a thin wrapper since T052's refactor (which
    lets `make_deterministic_node` call the same core logic directly,
    without going through the node) — the real logic, and this guarantee,
    live in `_apply_offer_agent`."""
    source = inspect.getsource(_apply_offer_agent)
    for forbidden in ("check_rg_status(", "list_casino_floor("):
        assert forbidden not in source, (
            f"_apply_offer_agent calls {forbidden} directly — its only vendor call must be "
            f"get_loyalty_status (require_gaming_clear wraps check_rg_status internally, which is fine)"
        )
    assert "get_loyalty_status" in source


@pytest.mark.asyncio
async def test_good_standing_guest_gets_offer_path_exercised():
    """The multi-domain (`SUPERVISOR`) path — `OFFER_AGENT` is mounted
    between `POLICY_POSTFLIGHT` and `RESPOND`."""
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    gaming = FakeGamingTools()
    factory = {
        Domain.DINING: lambda t, g: dining,
        Domain.ENTERTAINMENT: lambda t, g: entertainment,
        Domain.GAMING: lambda t, g: gaming,
    }
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-good-standing",
        text="book me dinner and get me tickets to a show tonight 2026-09-12",
    )
    config = thread_config("offer-good-standing-1")
    first = await app.ainvoke(state, config=config)
    assert first.get("__interrupt__")  # holds placed, paused at cross-domain HUMAN_APPROVAL

    result = await app.ainvoke(Command(resume={"approved": True}), config=config)

    assert "check_rg_status" in gaming.calls
    assert "get_loyalty_status" in gaming.calls
    # The offer message is OFFER_AGENT's own (RESPOND runs after it and
    # appends its own confirmation summary as a separate, later message).
    all_content = " ".join(m.content.lower() for m in result["messages"] if isinstance(m.content, str))
    assert "gold" in all_content or "member" in all_content


@pytest.mark.asyncio
async def test_self_excluded_guest_gets_zero_offers_and_loyalty_never_called():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    gaming = FakeGamingTools()
    factory = {
        Domain.DINING: lambda t, g: dining,
        Domain.ENTERTAINMENT: lambda t, g: entertainment,
        Domain.GAMING: lambda t, g: gaming,
    }
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-self-excluded",
        text="book me dinner and get me tickets to a show tonight 2026-09-12",
    )
    config = thread_config("offer-self-excluded-1")
    first = await app.ainvoke(state, config=config)
    assert first.get("__interrupt__")

    result = await app.ainvoke(Command(resume={"approved": True}), config=config)

    assert "check_rg_status" in gaming.calls
    assert "get_loyalty_status" not in gaming.calls
    content = result["messages"][-1].content.lower()
    assert "member" not in content
    assert "gold" not in content and "silver" not in content


@pytest.mark.asyncio
async def test_deterministic_path_also_reaches_offer_agent_good_standing():
    """T052 (feature 006 convergence, CRITICAL): a single-domain request —
    the `DETERMINISTIC` path, ~60% of traffic — must also gate through
    `require_gaming_clear` before any offer is considered, per Constitution
    Principle IV ("before the Offer Agent runs at all, on every turn").
    Before this fix, `DETERMINISTIC` routed straight to `END`, never
    reaching `OFFER_AGENT` at all."""
    dining = FakeDiningTools()
    gaming = FakeGamingTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.GAMING: lambda t, g: gaming}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-good-standing", text="table for 2 tomorrow at 7pm, steakhouse"
    )
    result = await app.ainvoke(state, config=thread_config("det-offer-good-1"))

    assert "check_rg_status" in gaming.calls
    assert "get_loyalty_status" in gaming.calls
    all_content = " ".join(m.content.lower() for m in result["messages"] if isinstance(m.content, str))
    assert "gold" in all_content or "member" in all_content
    # The booking itself must still be genuinely confirmed — the fix must
    # not have interfered with the inner dining subgraph's own outcome (the
    # double-messaging risk it had to avoid was about RESPOND re-summarizing
    # this, not about dining's own confirm step, which produces no message
    # of its own on success — `agents/dining/nodes/confirm.py` returns only
    # `holds`/`confirmations`, so the offer message being present alongside
    # a real confirmation is exactly the expected combined result).
    assert len(result["confirmations"]) == 1


@pytest.mark.asyncio
async def test_deterministic_path_self_excluded_guest_gets_no_offer():
    dining = FakeDiningTools()
    gaming = FakeGamingTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.GAMING: lambda t, g: gaming}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-self-excluded", text="table for 2 tomorrow at 7pm, steakhouse"
    )
    result = await app.ainvoke(state, config=thread_config("det-offer-selfexcl-1"))

    assert "check_rg_status" in gaming.calls
    assert "get_loyalty_status" not in gaming.calls
    all_content = " ".join(m.content.lower() for m in result["messages"] if isinstance(m.content, str))
    assert "member" not in all_content
    assert "gold" not in all_content and "silver" not in all_content
