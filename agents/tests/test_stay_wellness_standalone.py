"""T054 — standalone integration tests for stay and wellness (hold -> approve
-> confirm), each as its own single-domain request through `DETERMINISTIC`."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.stay.tests.fakes import FakeStayTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from agents.wellness.tests.fakes import FakeWellnessTools
from graph.models import Domain, HoldStatus
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_stay_only_request_completes_standalone():
    stay = FakeStayTools()
    factory = {Domain.STAY: lambda t, g: stay}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(tenant_id="MGV", guest_id="g1", text=f"a standard room on {FIXED_ISO_DATE} for 2 nights")
    cfg = thread_config("stay-standalone-1")
    result = await app.ainvoke(state, config=cfg)
    assert result["plan"] is None
    assert result.get("__interrupt__")

    result2 = await app.ainvoke(Command(resume={"approved": True}), config=cfg)
    assert len(result2["confirmations"]) == 1
    assert result2["confirmations"][0].domain == Domain.STAY
    assert result2["holds"][0].status == HoldStatus.CONFIRMED


@pytest.mark.asyncio
async def test_wellness_only_request_completes_standalone():
    wellness = FakeWellnessTools()
    factory = {Domain.WELLNESS: lambda t, g: wellness}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(tenant_id="MGV", guest_id="g1", text=f"a spa massage on {FIXED_ISO_DATE} for 1")
    cfg = thread_config("wellness-standalone-1")
    result = await app.ainvoke(state, config=cfg)
    assert result["plan"] is None
    assert result.get("__interrupt__")

    result2 = await app.ainvoke(Command(resume={"approved": True}), config=cfg)
    assert len(result2["confirmations"]) == 1
    assert result2["confirmations"][0].domain == Domain.WELLNESS
    assert result2["holds"][0].status == HoldStatus.CONFIRMED
