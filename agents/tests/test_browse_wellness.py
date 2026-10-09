"""Feature 011, T041 — User Story 5: spa and wellness services."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from agents.wellness.tests.fakes import FAKE_SERVICES, FakeWellnessTools
from agents.wellness.tools import WellnessToolError
from graph.models import Domain, RequestKind
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state

MGV = [s for s in FAKE_SERVICES if s.service_id.startswith("svc-mgv-")]


async def _ask(text: str, tools: FakeWellnessTools | None = None):
    tools = tools or FakeWellnessTools()
    factory = {Domain.WELLNESS: lambda t, g: tools, Domain.GAMING: lambda t, g: FakeGamingTools()}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())
    result = await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id="g1", text=text), config=thread_config(f"wl-{abs(hash(text))}")
    )
    assert result["routing"].kind == RequestKind.BROWSE
    assert not result.get("retrieved")
    return next(m.content for m in result["messages"] if m.type == "ai")


@pytest.mark.asyncio
async def test_services_are_listed_with_duration_and_price() -> None:
    reply = await _ask("list the spa and wellness services")
    assert "Signature Deep Tissue Massage — spa, 60 min, $180" in reply
    assert "Want to book one?" in reply


@pytest.mark.asyncio
async def test_a_category_filter_narrows() -> None:
    reply = await _ask("what spa treatments do you have")
    assert "Deep Tissue Massage" in reply
    assert "18-Hole Tee Time" not in reply and "Personal Training" not in reply


@pytest.mark.asyncio
async def test_a_provider_error_is_reported() -> None:
    class Failing(FakeWellnessTools):
        async def list_services(self, **_kw):
            raise WellnessToolError("boom", vendor_code="INTERNAL", http_status=500)

    reply = await _ask("what spa treatments do you have", Failing())
    assert "couldn't retrieve" in reply and "Massage" not in reply
