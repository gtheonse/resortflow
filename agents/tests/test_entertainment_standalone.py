"""T043 — an entertainment-only request (no dining/wellness in scope)
completes discover -> hold -> approve -> confirm with no other domain's
`PlanTask` present in the resulting `Plan` (spec.md User Story 3)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain, HoldStatus
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_entertainment_only_request_completes_standalone_with_no_plan():
    entertainment = FakeEntertainmentTools()
    factory = {Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    cfg = thread_config("entertainment-standalone-1")

    result = await app.ainvoke(state, config=cfg)
    assert result["plan"] is None, "single-domain requests never reach PLANNER — no PlanTask for any other domain"
    # The inner standalone subgraph's own hold/proposal writes are not
    # visible on this pass (a nested interrupt aborts the wrapping
    # DETERMINISTIC node's own return before it can copy them through —
    # see graph/supervisor.py's make_deterministic_node docstring); the
    # supported signal for "a decision is pending" here is `__interrupt__`.
    assert result.get("__interrupt__")

    result2 = await app.ainvoke(Command(resume={"approved": True}), config=cfg)

    assert len(result2["confirmations"]) == 1
    assert result2["confirmations"][0].domain == Domain.ENTERTAINMENT
    assert result2["holds"][0].status == HoldStatus.CONFIRMED
    assert "purchase_tickets" in entertainment.calls
