"""Feature 011, T034 — User Story 3: what is on, in a window (dates stated, stay period, defaults)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.entertainment.tests.fakes import FAKE_EVENTS, FakeEntertainmentTools
from agents.entertainment.tools import EntertainmentToolError
from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.bookings import BookingRecord
from graph.browse_node import BrowseContext, compose_browse_reply
from graph.models import DateWindow, Domain, RequestKind, RoutingDecision, RoutingTier
from graph.replies import browse_date, browse_reply_violations
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state

TODAY = date.today()  # noqa: DTZ011 - the graph under test uses the local wall-clock date too
TEMPLATE = next(e for e in FAKE_EVENTS if e.venue["tenant_id"] == "MGV")


def _event(n: int, days: int, name: str | None = None, status: str = "on_sale"):
    start = datetime.combine(TODAY + timedelta(days=days), time(20, 0), tzinfo=UTC) + timedelta(hours=7)
    return TEMPLATE.model_copy(
        update={"event_id": f"e{n}", "name": name or f"Show {n}", "start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": status}
    )


EVENTS = [
    _event(1, 1, "Alpha Show"),
    _event(2, 3, "Bravo Show"),
    _event(3, 2, "Sold Out Show", "sold_out"),
    _event(4, 2, "Cancelled Show", "cancelled"),
    _event(5, 6, "Later Show"),
    _event(6, 20, "Far Show"),
]


async def _ask(text: str, events=EVENTS, tools=None) -> str:
    tools = tools or FakeEntertainmentTools(events=events)
    factory = {Domain.ENTERTAINMENT: lambda t, g: tools, Domain.GAMING: lambda t, g: FakeGamingTools()}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())
    result = await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id="g1", text=text), config=thread_config(f"ev-{abs(hash(text))}")
    )
    assert result["routing"].kind == RequestKind.BROWSE
    return next(m.content for m in result["messages"] if m.type == "ai")


@pytest.mark.asyncio
async def test_next_five_days_filters_to_the_window_soonest_first_and_states_dates() -> None:
    reply = await _ask("what events are happening in the next 5 days")
    assert reply.index("Alpha Show") < reply.index("Bravo Show")
    assert "Later Show" not in reply and "Far Show" not in reply
    assert "Sold Out Show" not in reply and "Cancelled Show" not in reply
    assert browse_date(TODAY) in reply and browse_date(TODAY + timedelta(days=4)) in reply
    assert "Want tickets?" in reply
    assert browse_reply_violations(reply) == []


@pytest.mark.asyncio
async def test_no_dates_defaults_to_seven_days_and_says_so() -> None:
    reply = await _ask("what events are there")
    assert "(I used the next 7 days)" in reply
    assert "Later Show" in reply and "Far Show" not in reply


@pytest.mark.asyncio
async def test_nothing_in_the_window_offers_the_nearest_upcoming() -> None:
    reply = await _ask("what events are there", events=[_event(9, 20, "Far Show")])
    assert "couldn't find any events" in reply
    assert "Far Show" in reply and "next" in reply.lower()


@pytest.mark.asyncio
async def test_a_provider_error_is_reported_without_inventing_events() -> None:
    class Failing(FakeEntertainmentTools):
        async def search_events(self, **_kw):
            raise EntertainmentToolError("boom", vendor_code="INTERNAL", http_status=500)

    reply = await _ask("what events are there", tools=Failing(events=EVENTS))
    assert "couldn't retrieve" in reply and "Alpha Show" not in reply


@pytest.mark.asyncio
async def test_long_lists_are_capped_at_ten() -> None:
    many = [_event(i, 1 + i % 4, f"Crowd Show {i:02d}") for i in range(13)]
    reply = await _ask("what events are happening in the next 5 days", events=many)
    assert sum(1 for i in range(13) if f"Crowd Show {i:02d}" in reply) == 10
    assert "and 3 more" in reply


def _ctx(window: DateWindow, records: list[BookingRecord], tools: FakeEntertainmentTools) -> BrowseContext:
    routing = RoutingDecision(
        kind=RequestKind.BROWSE, services=[Domain.ENTERTAINMENT], tier=RoutingTier.KIND_RULES, window=window
    )
    return BrowseContext(
        routing=routing, tenant_id="MGV", guest_id="g1", text="events in my stay period", records=records,
        tools_factory={Domain.ENTERTAINMENT: lambda t, g: tools}, today=TODAY,
        window=window if window.source != "needs_dates" else None, needs_dates=window.source == "needs_dates",
    )


@pytest.mark.asyncio
async def test_stay_period_window_is_stated_as_the_guests_stay() -> None:
    window = DateWindow(start=TODAY + timedelta(days=1), end=TODAY + timedelta(days=3), source="stay_period")
    reply = await compose_browse_reply(_ctx(window, [], FakeEntertainmentTools(events=EVENTS)))
    assert "your stay:" in reply and "Alpha Show" in reply and "Later Show" not in reply


@pytest.mark.asyncio
async def test_needs_dates_asks_and_reads_nothing() -> None:
    tools = FakeEntertainmentTools(events=EVENTS)
    window = DateWindow(start=TODAY, end=TODAY, source="needs_dates")
    reply = await compose_browse_reply(_ctx(window, [], tools))
    assert "which dates" in reply and tools.calls == []


@pytest.mark.asyncio
async def test_a_message_with_stay_in_it_is_a_browse_not_a_lookup() -> None:
    reply = await _ask("what all events are happening in my stay period")
    assert "dates" in reply.lower()
