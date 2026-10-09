"""T068 [CRIT] — hold-revalidation-on-resume for entertainment: place a hold,
then have the vendor report it no longer active; confirm the supervisor's
`Commit` node re-queries the vendor (via entertainment's own `confirm.py`,
which `graph.supervisor.make_commit_node` delegates to — T068's fix for the
gap that node used to bypass) before ever calling `purchase_tickets`, rather
than trusting the in-memory `Hold.status == HELD` from state (FR-017;
AGENTS.md hard rule "Never trust state['holds'] on resume")."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain, HoldStatus
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_hold_expired_at_vendor_is_caught_before_purchase_not_trusted_from_state():
    entertainment = FakeEntertainmentTools()
    factory = {Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    # Two domain keywords ("dinner" + "show") force the SUPERVISOR/FanOut/
    # HumanApproval/Commit saga path (a single-domain request would route to
    # DETERMINISTIC instead, bypassing the Commit node T068 fixes) — dining
    # is deliberately absent from `factory`, so it contributes no proposal
    # and this stays an entertainment-only assertion.
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    cfg = thread_config("revalidate-1")

    result = await app.ainvoke(state, config=cfg)
    assert result["awaiting_approval"] is True
    assert result["holds"][0].status == HoldStatus.HELD

    # Simulate the hold going stale at the vendor between approval and
    # commit (e.g. TTL lapsed while the guest was deciding) — state still
    # says HELD, but the vendor no longer agrees.
    entertainment.hold_expired_on_revalidate = True

    result2 = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    assert "get_order_status" in entertainment.calls, "must revalidate against the vendor before purchasing"
    assert "purchase_tickets" not in entertainment.calls, "must never purchase a hold state alone claims is still held"
    assert result2["confirmations"] == []
    entertainment_holds = [h for h in result2["holds"] if h.domain == Domain.ENTERTAINMENT]
    assert entertainment_holds[0].status == HoldStatus.EXPIRED
