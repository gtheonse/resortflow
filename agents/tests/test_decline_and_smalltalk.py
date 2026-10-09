"""Feature 010, T060 — US3: `DECLINE` and `SMALLTALK` (FR-018/FR-019,
SC-002, SC-010). Must fail before T054/T062."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FakeDiningTools
from graph.models import Domain
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state


def _reply(result: dict) -> str:
    messages = result.get("messages") or []
    return getattr(messages[-1], "content", "") if messages else ""


@pytest.mark.asyncio
async def test_verbatim_sc010_decline_lists_services_no_knowledge_text():
    dining = FakeDiningTools()
    factory = {Domain.DINING: lambda t, g: dining}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())

    state = new_session_state(tenant_id="MGV", guest_id="g1", text="book flight for me to fly to India")
    cfg = {"configurable": {"thread_id": "decline-1"}}
    result = await app.ainvoke(state, config=cfg)

    reply = _reply(result).lower()
    assert "can't" in reply or "cannot" in reply
    assert "dining" in reply or "entertainment" in reply or "wellness" in reply
    assert result.get("retrieved") in (None, [])  # no knowledge-base text
    assert result["confirmations"] == []


@pytest.mark.asyncio
async def test_flight_with_dinner_on_board_declines_without_booking_dining():
    dining = FakeDiningTools()
    factory = {Domain.DINING: lambda t, g: dining}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())

    state = new_session_state(tenant_id="MGV", guest_id="g1", text="book a flight with dinner on board")
    cfg = {"configurable": {"thread_id": "decline-2"}}
    result = await app.ainvoke(state, config=cfg)

    assert result["confirmations"] == []
    assert result["holds"] == []


@pytest.mark.asyncio
async def test_smalltalk_no_retrieval_no_classifier_call():
    calls: list[str] = []

    class _SpyClassifier:
        async def classify(self, text, *, previous=None):
            calls.append(text)
            return await UnavailableClassifier().classify(text, previous=previous)

    # Non-empty factory with no Domain.GAMING entry — same shape as this
    # file's other two tests. `build_supervisor_graph` falls back to the
    # real `DEFAULT_TOOLS_FACTORY` via `tools_factory or DEFAULT_TOOLS_
    # FACTORY`, and an *empty* dict is falsy in Python, so `{}` here would
    # trigger that fallback too — its gaming entry makes a live vendor call
    # from `_apply_offer_agent`'s self-exclusion check (it runs on every
    # turn, including smalltalk) and fails with a connection error in this
    # test's fake-tools-only environment. A dummy non-gaming key keeps the
    # dict truthy without adding a real Domain.GAMING entry.
    dining = FakeDiningTools()
    factory: dict[Domain, object] = {Domain.DINING: lambda t, g: dining}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=_SpyClassifier())
    state = new_session_state(tenant_id="MGV", guest_id="g1", text="thanks")
    cfg = {"configurable": {"thread_id": "smalltalk-1"}}
    result = await app.ainvoke(state, config=cfg)

    assert calls == []
    assert result.get("retrieved") in (None, [])
