"""Feature 011, T030 — one-turn refinement of a browse (FR-005a, SC-011)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FAKE_VENUES, FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.models import Domain, RequestKind
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state

MGV_NAMES = {v.name for v in FAKE_VENUES if v.tenant_id == "MGV"}


def _app():
    fakes = {
        Domain.DINING: FakeDiningTools(),
        Domain.ENTERTAINMENT: FakeEntertainmentTools(),
        Domain.GAMING: FakeGamingTools(),
    }
    factory = {d: (lambda t, g, f=f: f) for d, f in fakes.items()}
    return compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())


async def _follow(app, cfg, text: str):
    return await app.ainvoke({"messages": [HumanMessage(content=text)]}, config=cfg)


@pytest.mark.asyncio
async def test_only_japanese_right_after_a_dining_browse_narrows_it() -> None:
    app, cfg = _app(), thread_config("refine-dining")
    first = await app.ainvoke(new_session_state(tenant_id="MGV", guest_id="g1", text="list the restaurants"), config=cfg)
    assert {n for n in MGV_NAMES if n in first["messages"][-1].content} == MGV_NAMES
    second = await _follow(app, cfg, "only japanese")
    assert second["routing"].kind == RequestKind.BROWSE
    reply = second["messages"][-1].content
    assert {n for n in MGV_NAMES if n in reply} == {"Sakura Sushi Bar"}


@pytest.mark.asyncio
async def test_cheaper_ones_after_an_events_browse_stays_on_events() -> None:
    app, cfg = _app(), thread_config("refine-events")
    await app.ainvoke(new_session_state(tenant_id="MGV", guest_id="g1", text="what events are on this weekend"), config=cfg)
    second = await _follow(app, cfg, "cheaper ones")
    assert second["routing"].kind == RequestKind.BROWSE
    assert second["routing"].services == [Domain.ENTERTAINMENT]


@pytest.mark.asyncio
async def test_after_an_intervening_turn_the_follow_up_is_a_fresh_message() -> None:
    app, cfg = _app(), thread_config("refine-stale")
    await app.ainvoke(new_session_state(tenant_id="MGV", guest_id="g1", text="list the restaurants"), config=cfg)
    await _follow(app, cfg, "thanks")
    third = await _follow(app, cfg, "only japanese")
    assert third["routing"].kind != RequestKind.BROWSE or not third["routing"].browse_filters
    assert {n for n in MGV_NAMES if n in third["messages"][-1].content} != {"Sakura Sushi Bar"}
