"""T055 [CRIT] — a request phrased as a wager/deposit/account action is
refused clearly, never routed to any mutating call — there is none to route
to (Constitution Principle II). Supervisor-level: proves gaming's read-only
guarantee holds even when a request reaches gaming through `DETERMINISTIC`,
not just within `agents/gaming`'s own isolated tests."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_wager_phrased_request_is_refused_not_routed_to_a_mutation():
    gaming = FakeGamingTools()
    factory = {Domain.GAMING: lambda t, g: gaming}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="guest-good-standing",
        text="place a $500 bet on blackjack for me and deposit $1000 into my account",
    )
    cfg = thread_config("gaming-refusal-1")
    result = await app.ainvoke(state, config=cfg)

    # No hold, no confirmation, no failure claiming a transaction was
    # attempted — the request lands on a read-only lookup, full stop.
    assert result["holds"] == []
    assert result["confirmations"] == []
    assert not result.get("__interrupt__")
    # Only the three declared read-only calls ever exist on the fake; a
    # mutating call is structurally impossible, not merely unobserved.
    assert set(gaming.calls) <= {"check_rg_status", "get_loyalty_status", "list_casino_floor"}
