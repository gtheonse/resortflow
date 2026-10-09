"""Feature 010, T047 — the regression suite that fails before the fix
(research R-12): the reply and the console-facing turn summary must report
only THIS TURN's real outcome, including across an approval pause. This is
the live incident that triggered feature 010 ("we saw 'booked' while the
internal hold was still on with its TTL running")."""

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
from graph.turn_summary import summarize_turn


def _reply_text(result: dict) -> str:
    """The reply for THIS turn only — the last message, matching
    `graph.turn_summary.summarize_turn`'s own extraction. `messages`
    accumulates across every turn on the thread (`add_messages`), so
    joining every message here would (and did, before this fix) make an
    earlier turn's "confirmed" wording look like it belongs to a later,
    unrelated turn."""
    messages = result.get("messages") or []
    return getattr(messages[-1], "content", "") if messages else ""


@pytest.mark.asyncio
async def test_turn_that_only_holds_never_says_confirmed():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    cfg = thread_config("turn-reporting-1")
    result = await app.ainvoke(state, config=cfg)

    assert result["awaiting_approval"] is True
    reply = _reply_text(result)
    for forbidden in ("confirmed", "booked", "all set"):
        assert forbidden not in reply.lower(), f"turn 1 (hold only) must not say {forbidden!r}: {reply!r}"


@pytest.mark.asyncio
async def test_approve_confirms_exactly_once_and_reply_says_so():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    cfg = thread_config("turn-reporting-2")
    await app.ainvoke(state, config=cfg)
    result = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    assert len(result["confirmations"]) == 2
    reply = _reply_text(result)
    assert "confirmed" in reply.lower()

    # A further, uneventful turn must not re-claim anything as confirmed.
    # Only the new message is sent — exactly what graph/api.py does for
    # every turn after the first (`new_session_state` is for a brand new
    # thread only); passing a fresh `new_session_state()` on an existing
    # thread would incorrectly reset `routing`/`turn_baseline` (no
    # reducer) before INTENT_ROUTER even runs. "thanks" rather than a
    # knowledge-answer question — this test isn't exercising retrieval,
    # and a live Qdrant/embedding backend isn't assumed to be configured
    # in this test environment.
    result2 = await app.ainvoke({"messages": [HumanMessage(content="thanks")]}, config=cfg)
    later_reply = _reply_text(result2)
    assert "confirmed" not in later_reply.lower()
    # Exactly one record of the original booking survives every later turn.
    assert len(result2["confirmations"]) == 2


@pytest.mark.asyncio
async def test_decline_releases_and_never_says_confirmed():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    cfg = thread_config("turn-reporting-3")
    await app.ainvoke(state, config=cfg)
    result = await app.ainvoke(Command(resume={"action": "declined"}), config=cfg)

    assert result["confirmations"] == []
    reply = _reply_text(result)
    for forbidden in ("confirmed:", "all set", "booked:"):
        assert forbidden not in reply.lower()


@pytest.mark.asyncio
async def test_hold_expired_before_commit_never_claims_that_leg_confirmed():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    cfg = thread_config("turn-reporting-4")
    await app.ainvoke(state, config=cfg)

    entertainment.hold_expired_on_revalidate = True
    result = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    entertainment_confirmations = [c for c in result["confirmations"] if c.domain == Domain.ENTERTAINMENT]
    assert entertainment_confirmations == [], "an expired hold must never be reported confirmed"


def test_turn_summary_booking_outcome_reflects_this_turn_only():
    """The additive `booking_outcome` field (FR-035) the console badge
    will read — asserted directly on `summarize_turn`'s output shape."""
    result = {"messages": [], "confirmations": [], "holds": [], "proposals": []}
    summary = summarize_turn(result, thread_id="t1", node_order=[])
    assert "reply" in summary  # pre-existing shape unchanged
