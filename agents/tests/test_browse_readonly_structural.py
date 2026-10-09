"""Feature 011, T023 — the read-only guarantee (Constitution Principle III, FR-015, SC-009).

A browse never places a hold, reserves, purchases, changes or cancels anything, and never places a wager. Checked
two ways: (1) structurally, the browse node's source references no mutating tool method and never reads
`state["retrieved"]`; (2) behaviourally, through the compiled graph with fake tools that record every call, only
read methods are ever invoked and no booking state is written.

Must fail before T024 (the node)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.gaming.tests.fakes import FakeGamingTools
from agents.stay.tests.fakes import FakeStayTools
from agents.tests.conftest import thread_config
from agents.wellness.tests.fakes import FakeWellnessTools
from graph.models import Domain
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state

NODE_SOURCE = Path(__file__).resolve().parents[2] / "graph" / "browse_node.py"

FORBIDDEN_CALL = re.compile(
    r"\.\s*(hold_\w+|confirm_\w+|purchase_\w+|cancel_\w+|release_\w+|join_waitlist|request_\w+|create_\w+)\s*\("
)
ALLOWED_READS = frozenset(
    {"list_venues", "search_events", "list_services", "search_rooms", "check_rg_status", "list_casino_floor",
     "get_loyalty_status"}
)


def test_the_browse_node_source_calls_no_mutating_tool_method() -> None:
    source = NODE_SOURCE.read_text(encoding="utf-8")
    assert not FORBIDDEN_CALL.search(source), FORBIDDEN_CALL.search(source)


def test_the_browse_node_never_reads_retrieved_knowledge_base_text() -> None:
    source = NODE_SOURCE.read_text(encoding="utf-8")
    assert '"retrieved"' not in source and "'retrieved'" not in source
    assert "Chunk" not in source


BROWSES = [
    "what are the dining venues",
    "what events are happening in the next 5 days",
    "list the resort experiences",
    "what spa treatments do you have",
    "what rooms are available oct 5 to oct 8",
    "what casino games do you have",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", BROWSES)
async def test_a_browse_turn_only_reads_and_writes_no_booking_state(text: str) -> None:
    fakes = {
        Domain.DINING: FakeDiningTools(),
        Domain.ENTERTAINMENT: FakeEntertainmentTools(),
        Domain.WELLNESS: FakeWellnessTools(),
        Domain.STAY: FakeStayTools(),
        Domain.GAMING: FakeGamingTools(),
    }
    factory = {domain: (lambda t, g, fake=fake: fake) for domain, fake in fakes.items()}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())
    state = new_session_state(tenant_id="MGV", guest_id="g1", text=text)
    result = await app.ainvoke(state, config=thread_config(f"readonly-{abs(hash(text))}"))

    called = {call for fake in fakes.values() for call in fake.calls}
    assert called <= ALLOWED_READS, f"{text!r} called non-read methods: {called - ALLOWED_READS}"
    assert not result.get("holds")
    assert not result.get("proposals")
    assert not result.get("confirmations")
    assert not result.get("retrieved")
    assert result.get("awaiting_approval") in (False, None)
    assert not any(getattr(h, "hold_id", None) for h in fakes[Domain.DINING].holds.values())
    assert not fakes[Domain.DINING].reservations and not fakes[Domain.ENTERTAINMENT].orders
    assert not fakes[Domain.WELLNESS].bookings and not fakes[Domain.STAY].reservations
