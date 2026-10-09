"""Allergen policy must behave identically on the standalone and fan-out dining
paths: a blocking conflict places NO vendor hold on either (ADR-029, plan step 1).

Before unification the fan-out leg blocked before `hold_table`, while the
standalone subgraph placed the hold first and left it to the TTL reaper.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.graph import compile_dining_graph
from agents.dining.graph import new_session_state as dining_session
from agents.dining.tests.fakes import FakeDiningTools
from agents.dining.tests.test_graph import make_extractor
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state

_DINING_TEXT = "table for 2 tomorrow at 7pm, japanese, severe peanut allergy"


@pytest.mark.asyncio
async def test_standalone_blocked_allergen_places_no_hold() -> None:
    tools = FakeDiningTools()
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = dining_session(tenant_id="MGV", guest_id="g1", text=_DINING_TEXT)
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "parity-standalone"}})

    assert result["policy_blocked"] is True
    assert "hold_table" not in tools.calls
    assert result["confirmations"] == []


@pytest.mark.asyncio
async def test_fanout_blocked_allergen_places_no_hold() -> None:
    dining = FakeDiningTools()
    factory = {
        Domain.DINING: lambda t, g: dining,
        Domain.ENTERTAINMENT: lambda t, g: FakeEntertainmentTools(),
    }
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"japanese dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2, severe peanut allergy",
    )
    result = await app.ainvoke(state, config=thread_config("parity-fanout"))

    assert "hold_table" not in dining.calls
    assert not [h for h in result.get("holds", []) if h.domain == Domain.DINING]
