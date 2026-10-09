"""Feature 011, T037 — User Story 4: rooms and stay length."""

from __future__ import annotations

from datetime import date

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.gaming.tests.fakes import FakeGamingTools
from agents.stay.nodes.intent_parser import RuleBasedIntentExtractor
from agents.stay.tests.fakes import FAKE_OFFERS, FakeStayTools
from agents.tests.conftest import thread_config
from graph.models import Domain, RequestKind
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state


async def _ask(text: str, tools: FakeStayTools | None = None) -> tuple[str, FakeStayTools]:
    tools = tools or FakeStayTools()
    factory = {Domain.STAY: lambda t, g: tools, Domain.GAMING: lambda t, g: FakeGamingTools()}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())
    result = await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id="g1", text=text), config=thread_config(f"st-{abs(hash(text))}")
    )
    assert result["routing"].kind == RequestKind.BROWSE
    return next(m.content for m in result["messages"] if m.type == "ai"), tools


@pytest.mark.asyncio
async def test_rooms_are_listed_with_nightly_rate_and_stated_dates() -> None:
    reply, tools = await _ask("what rooms are available oct 5 to oct 8")
    assert "Standard Room — from $249/night" in reply and "Deluxe King — from $349/night" in reply
    assert "Oct 5 to Oct 8" in reply and "3 nights" in reply
    assert "(I assumed 2 adults)" in reply
    assert tools.calls == ["search_rooms"]


@pytest.mark.asyncio
async def test_rooms_with_no_units_left_are_omitted() -> None:
    sold = [FAKE_OFFERS[0].model_copy(update={"AVAILABLE_UNITS": 0}), FAKE_OFFERS[1]]
    reply, _ = await _ask("what rooms are available oct 5 to oct 8", FakeStayTools(offers=sold))
    assert "Standard Room" not in reply and "Deluxe King" in reply


@pytest.mark.asyncio
async def test_missing_dates_asks_and_reads_nothing() -> None:
    reply, tools = await _ask("what rooms do you have")
    assert "Which dates?" in reply and tools.calls == []


def test_book_stay_for_next_five_days_is_a_five_night_intent_asking_only_for_arrival() -> None:
    parsed = RuleBasedIntentExtractor().extract("book stay for next 5 days", today=date(2026, 9, 30))
    assert parsed["nights"] == 5
    assert "date_str" in parsed["ambiguous_fields"]


def test_an_explicit_date_range_sets_arrival_and_the_night_count() -> None:
    parsed = RuleBasedIntentExtractor().extract("book a room for oct 5 to oct 8 for 2", today=date(2026, 9, 30))
    assert parsed["date_str"] == "2026-10-05" and parsed["nights"] == 3
    assert "date_str" not in parsed["ambiguous_fields"]


def test_a_single_date_or_a_length_phrase_is_not_mistaken_for_a_range() -> None:
    extractor = RuleBasedIntentExtractor()
    assert extractor.extract("book a room on oct 5", today=date(2026, 9, 30))["nights"] == 1
    assert extractor.extract("book a room for 3 nights on oct 5", today=date(2026, 9, 30))["nights"] == 3


async def _book(text: str):
    from datetime import timedelta

    from graph.turn_summary import summarize_turn

    tools = FakeStayTools()
    factory = {Domain.STAY: lambda t, g: tools, Domain.GAMING: lambda t, g: FakeGamingTools()}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())
    result = await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id="g1", text=text), config=thread_config(f"bk-{abs(hash(text))}")
    )
    summary = summarize_turn(result, thread_id="t", node_order=[])
    return summary, date.today() + timedelta(days=1)  # noqa: DTZ011 - the stay flow uses the local wall-clock date


@pytest.mark.asyncio
async def test_a_stay_with_a_length_and_no_arrival_states_the_tomorrow_default() -> None:
    from graph.replies import browse_date

    summary, tomorrow = await _book("book stay for next 5 days")
    assert summary["interrupt"]["kind"] == "approval"
    reply = summary["reply"]
    assert "tomorrow" in reply and browse_date(tomorrow) in reply and "5 nights" in reply
    assert "decline and tell me the arrival date" in reply and "Please review and approve" in reply


@pytest.mark.asyncio
async def test_a_stay_with_an_explicit_arrival_has_no_assumption_note() -> None:
    summary, _ = await _book("book a room for oct 5 to oct 8 for 2")
    assert "tomorrow" not in summary["reply"] and "note" not in summary["interrupt"]
