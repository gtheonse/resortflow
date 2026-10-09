"""Feature 011, T031 — overview / clarify interplay (US2, FR-023)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.models import Domain, RequestKind
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state


def _app():
    factory = {Domain.DINING: lambda t, g: FakeDiningTools(), Domain.GAMING: lambda t, g: FakeGamingTools()}
    return compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())


async def _nodes(app, payload, cfg) -> list[str]:
    seen: list[str] = []
    async for update in app.astream(payload, config=cfg, stream_mode="updates"):
        seen.extend(update.keys())
    return seen


@pytest.mark.asyncio
async def test_a_first_message_overview_goes_intent_router_then_browse() -> None:
    app, cfg = _app(), thread_config("cl-first")
    nodes = await _nodes(
        app, new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text="list the resort experiences"), cfg
    )
    real = [n for n in nodes if n in ("intent_router", "browse")]
    assert real[:2] == ["intent_router", "browse"]


def _overview_text(result) -> str:
    return next((m.content for m in result["messages"] if "Here's what I can arrange" in str(m.content)), "")


@pytest.mark.asyncio
async def test_as_a_clarify_answer_it_is_an_overview_and_resets_the_count() -> None:
    app, cfg = _app(), thread_config("cl-answer")
    first = await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text="book me a nice evening"), config=cfg
    )
    question = first["__interrupt__"][0].value["question"]
    assert "dining" in question.lower() and "spa" in question.lower()
    second = await app.ainvoke(Command(resume="list the resort experiences"), config=cfg)
    assert second["routing"].kind == RequestKind.BROWSE
    assert second["clarification_count"] == 0
    assert _overview_text(second)


@pytest.mark.asyncio
async def test_consecutive_browse_answers_never_escalate() -> None:
    app, cfg = _app(), thread_config("cl-twice")
    await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text="list the resort experiences"), config=cfg
    )
    second = await app.ainvoke({"messages": [HumanMessage(content="list the resort experiences")]}, config=cfg)
    assert second["clarification_count"] == 0
    assert second["routing"].kind == RequestKind.BROWSE


@pytest.mark.asyncio
async def test_a_bare_service_after_the_overview_continues_into_booking() -> None:
    app, cfg = _app(), thread_config("cl-continue")
    await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id="guest-good-standing", text="list the resort experiences"), config=cfg
    )
    third = await app.ainvoke({"messages": [HumanMessage(content="dining")]}, config=cfg)
    assert third["routing"].kind == RequestKind.BOOK
    assert Domain.DINING in third["routing"].services
