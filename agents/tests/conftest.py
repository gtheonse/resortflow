"""Shared fixtures for supervisor-level tests (`agents/tests/`) — tests that
exercise `graph.supervisor` across multiple domains at once, as distinct from
each domain's own `agents/<domain>/tests/` which test that domain in
isolation. See `graph/supervisor.py`'s own module docstring for why these
are two different things."""

from __future__ import annotations

from datetime import date

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.wellness.tests.fakes import FakeWellnessTools
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph

TODAY = date(2026, 9, 11)
FIXED_ISO_DATE = "2026-09-12"
"""`graph.supervisor`'s FanOut runners construct each domain's
`RuleBasedIntentExtractor` directly (not via a test-only fixed-`today`
subclass the way each domain's own isolated tests do), so supervisor-level
tests sidestep the "tonight"/"tomorrow" resolution entirely by using an
explicit ISO date in request text, which every domain's extractor parses
deterministically regardless of the real wall-clock date."""


@pytest.fixture
def domain_fakes():
    return {
        Domain.DINING: FakeDiningTools(),
        Domain.ENTERTAINMENT: FakeEntertainmentTools(),
        Domain.WELLNESS: FakeWellnessTools(),
    }


@pytest.fixture
def app_and_fakes(domain_fakes):
    factory = {domain: (lambda t, g, tool=tool: tool) for domain, tool in domain_fakes.items()}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)
    return app, domain_fakes


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}
