"""A gambling-distress handoff surfaces the jurisdiction's responsible-gaming resources (Constitution IV).

"Any utterance suggesting gambling-related distress MUST route to human handoff with responsible-gaming resources
surfaced." The route existed; the handoff text was generic and surfaced none. Found by the gaming adversarial suite
(turn 30, failing since feature 010)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.models import Domain
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state

DISTRESS = "what casino gambling problem resources are posted"


async def _run(tenant: str, text: str, name: str):
    gaming = FakeGamingTools()
    app = compile_supervisor_graph(
        checkpointer=MemorySaver(), tools_factory={Domain.GAMING: lambda t, g: gaming}, classifier=UnavailableClassifier()
    )
    result = await app.ainvoke(
        new_session_state(tenant_id=tenant, guest_id="g1", text=text), config=thread_config(name)
    )
    return result, "\n".join(str(m.content) for m in result["messages"] if m.type == "ai"), gaming


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tenant,helpline", [("MGV", "1-800-522-4700"), ("MHB", "1-800-327-5050")]
)
async def test_a_distress_handoff_surfaces_its_own_jurisdictions_helpline(tenant: str, helpline: str) -> None:
    result, reply, gaming = await _run(tenant, DISTRESS, f"dh-{tenant}")
    assert result["escalate"] is True and result["escalate_reason"] == "distress_signal"
    assert "handed off to a team member" in reply
    assert helpline in reply
    other = "1-800-327-5050" if tenant == "MGV" else "1-800-522-4700"
    assert other not in reply  # never the other jurisdiction's wording
    assert gaming.calls == []  # a handoff stops automated processing: no gaming tool at all


@pytest.mark.asyncio
async def test_a_non_distress_handoff_carries_no_gaming_text() -> None:
    from langgraph.types import Command

    from graph.state import MAX_CLARIFICATION_COUNT

    gaming = FakeGamingTools()
    app = compile_supervisor_graph(
        checkpointer=MemorySaver(), tools_factory={Domain.GAMING: lambda t, g: gaming}, classifier=UnavailableClassifier()
    )
    cfg = thread_config("dh-clarify")
    result = await app.ainvoke(new_session_state(tenant_id="MGV", guest_id="g1", text="book me a nice evening"), config=cfg)
    for _ in range(MAX_CLARIFICATION_COUNT):
        result = await app.ainvoke(Command(resume="still not sure, surprise me"), config=cfg)
    reply = "\n".join(str(m.content) for m in result["messages"] if m.type == "ai")
    assert result["escalate_reason"] == "clarification_bound"
    assert "1-800" not in reply and "gambling" not in reply.lower()
