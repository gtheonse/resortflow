"""T036 — verifies the same structured-enum-plus-severity dietary enforcement
Phase 4 built for dining-only requests still runs unchanged when dining is
one leg of a multi-domain plan (spec.md's clarified FR-019): the dietary
requirement extracted by dining's own intent parser must reach
`confirm_reservation` as a structured value, never dropped when dining runs
inside the cross-domain FanOut/Commit path instead of its own standalone
graph."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_severe_allergy_reaches_confirm_reservation_inside_multidomain_commit():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2, severe peanut allergy",
    )
    cfg = thread_config("dietary-1")
    await app.ainvoke(state, config=cfg)
    result = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    dining_proposal = next(p for p in result["proposals"] if p.domain == Domain.DINING)
    assert dining_proposal.dietary, "the structured dietary requirement must survive onto the Proposal"
    assert dining_proposal.dietary[0].restriction.value == "peanut"
    assert dining_proposal.dietary[0].severity.value == "allergy_severe"

    reservation_call_dietary = dining.reservations[next(iter(dining.reservations))].dietary
    assert "peanut" in reservation_call_dietary, "the structured restriction must reach confirm_reservation, not just intent"
