"""T056 [CRIT] — quickstart.md Scenario 4: force every domain unavailable,
assert exactly `MAX_REPLAN_COUNT` replan cycles, then `escalate=True`,
`escalate_reason="replan_bound"`, `HumanHandoff` reached. The conversation
must not attempt a fourth replan (spec.md User Story 5)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FakeDiningTools
from agents.dining.tools import DiningToolError
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.entertainment.tools import EntertainmentToolError
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.state import MAX_REPLAN_COUNT
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_every_domain_unavailable_exhausts_replan_and_hands_off():
    dining = FakeDiningTools(
        fail_availability=DiningToolError("no venues", vendor_code="SLOT_UNAVAILABLE", http_status=409)
    )
    entertainment = FakeEntertainmentTools(
        fail_search=EntertainmentToolError("no events", vendor_code="INVENTORY_UNAVAILABLE", http_status=409)
    )
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
    )
    cfg = thread_config("exhaustion-1")
    result = await app.ainvoke(state, config=cfg)

    assert result["escalate"] is True
    assert result["escalate_reason"] == "replan_bound"
    assert result["replan_count"] > MAX_REPLAN_COUNT
    assert not result.get("__interrupt__"), "must reach HumanHandoff, not pause for an approval that will never come"
    content = result["messages"][-1].content.lower()
    assert "team member" in content


@pytest.mark.asyncio
async def test_followup_message_after_handoff_does_not_resume_automated_planning():
    """T057 — spec.md User Story 5 Acceptance Scenario 2: after HumanHandoff,
    a follow-up guest message on the same thread does not trigger a new
    automated Planner cycle."""
    dining = FakeDiningTools(
        fail_availability=DiningToolError("no venues", vendor_code="SLOT_UNAVAILABLE", http_status=409)
    )
    entertainment = FakeEntertainmentTools(
        fail_search=EntertainmentToolError("no events", vendor_code="INVENTORY_UNAVAILABLE", http_status=409)
    )
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
    )
    cfg = thread_config("exhaustion-2")
    first = await app.ainvoke(state, config=cfg)
    assert first["escalate"] is True
    replan_count_after_handoff = first["replan_count"]

    from langchain_core.messages import HumanMessage

    followup = await app.ainvoke({"messages": [HumanMessage(content="hello, still there?")]}, config=cfg)

    assert followup["escalate"] is True, "must not silently clear escalation and resume automated processing"
    assert followup["replan_count"] == replan_count_after_handoff, "no new Planner cycle may run after handoff"
