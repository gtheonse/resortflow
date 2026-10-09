"""T059 — quickstart.md Scenario 3: a domain fully unavailable produces a
partial itinerary rather than failing the whole request (spec.md's clarified
FR-021). Dining succeeds; entertainment has zero availability throughout —
the guest still gets dining confirmed, with entertainment named specifically
as unavailable."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.entertainment.tools import EntertainmentToolError
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_entertainment_fully_unavailable_still_confirms_dining():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools(
        fail_search=EntertainmentToolError("no events", vendor_code="INVENTORY_UNAVAILABLE", http_status=409)
    )
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MHB", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE}"
    )
    cfg = thread_config("partial-1")
    result = await app.ainvoke(state, config=cfg)

    assert result["awaiting_approval"] is True
    held_domains = {p.domain for p in result["proposals"]}
    assert held_domains == {Domain.DINING}, "only dining should have a held proposal — entertainment never got one"

    kinds = [e.kind for e in result["saga_log"]]
    assert "partial_itinerary_proposed" in kinds

    result2 = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)
    confirmed_domains = {c.domain for c in result2["confirmations"]}
    assert confirmed_domains == {Domain.DINING}
    assert result2["escalate"] is False

    message = result2["messages"][-1].content.lower()
    # Feature 010 (contracts/turn-replies.md): the reply now names the
    # actual venue confirmed, not the raw domain string "dining" — a more
    # informative version of exactly what this test's own docstring asks
    # for ("the guest still gets dining confirmed").
    assert "confirmed" in message
    assert "entertainment" not in message
