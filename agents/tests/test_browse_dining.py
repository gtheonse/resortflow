"""Feature 011, T026 — User Story 1: see the dining venues before booking.

Through the compiled graph with fake tools; asserts the reply, not the internals. Must fail before T027-T029."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FAKE_VENUES, FakeDiningTools
from agents.dining.tools import DiningToolError
from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.models import Domain, RequestKind
from graph.replies import browse_reply_violations
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state

MGV_NAMES = {v.name for v in FAKE_VENUES if v.tenant_id == "MGV"}
MHB_NAMES = {v.name for v in FAKE_VENUES if v.tenant_id == "MHB"}


async def _ask(text: str, *, tenant: str = "MGV", dining: FakeDiningTools | None = None):
    dining = dining or FakeDiningTools(tenant_id=tenant)
    factory = {Domain.DINING: lambda t, g: dining, Domain.GAMING: lambda t, g: FakeGamingTools()}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())
    state = new_session_state(tenant_id=tenant, guest_id="g1", text=text)
    result = await app.ainvoke(state, config=thread_config(f"dining-{tenant}-{abs(hash(text))}"))
    reply = result["messages"][-1].content
    return result, reply


def _names_in(reply: str, names: set[str]) -> set[str]:
    return {n for n in names if n in reply}


@pytest.mark.asyncio
async def test_the_reported_question_lists_the_guests_own_venues() -> None:
    result, reply = await _ask("what are the dining venues available in nevada")
    assert result["routing"].kind == RequestKind.BROWSE
    assert _names_in(reply, MGV_NAMES) == MGV_NAMES
    assert not _names_in(reply, MHB_NAMES)
    assert "Meridian Grand" in reply
    assert "steakhouse" in reply and "$$$$" in reply
    assert "Want me to book one?" in reply
    assert "allergen" not in reply.lower() and not result.get("retrieved")
    assert browse_reply_violations(reply) == []


@pytest.mark.asyncio
async def test_the_other_tenant_gets_only_its_own_venues() -> None:
    _result, reply = await _ask("what are the dining venues available in boston", tenant="MHB")
    assert _names_in(reply, MHB_NAMES) == MHB_NAMES
    assert not _names_in(reply, MGV_NAMES)
    assert "Meridian Harbor" in reply and "Meridian Grand" not in reply
    assert "options only" not in reply  # boston is MHB's own region


@pytest.mark.asyncio
async def test_another_property_named_adds_the_property_statement_but_shows_the_guests_own() -> None:
    _result, reply = await _ask("what are the dining venues available in boston", tenant="MGV")
    assert reply.startswith("I can show Meridian Grand's options only.")
    assert _names_in(reply, MGV_NAMES) == MGV_NAMES and not _names_in(reply, MHB_NAMES)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,expected",
    [
        ("any japanese restaurants tonight", {"Sakura Sushi Bar"}),
        ("list the mexican restaurants", {"Cactus Flower Cantina"}),
        ("which restaurants are at $$", {"Cactus Flower Cantina", "Neon Noodle House"}),
        ("list restaurants under $$$", {"Cactus Flower Cantina", "Neon Noodle House"}),
        ("list the cheap restaurants", {"Cactus Flower Cantina", "Neon Noodle House"}),
        ("which upscale restaurants do you have", {"Meridian Steakhouse", "Sakura Sushi Bar", "The Green Table"}),
    ],
)
async def test_filters_narrow_the_list(text: str, expected: set[str]) -> None:
    _result, reply = await _ask(text)
    assert _names_in(reply, MGV_NAMES) == expected, reply


@pytest.mark.asyncio
async def test_a_dietary_need_narrows_and_never_claims_safety() -> None:
    _result, reply = await _ask("which restaurants have vegetarian options")
    assert _names_in(reply, MGV_NAMES) == {"The Green Table"}
    assert "You state your dietary restriction when you book" in reply
    assert browse_reply_violations(reply) == []
    _result, peanut = await _ask("which restaurants are good for a peanut allergy")
    assert "You state your dietary restriction when you book" in peanut
    assert browse_reply_violations(peanut) == []


@pytest.mark.asyncio
async def test_no_match_says_so_and_invents_nothing() -> None:
    _result, reply = await _ask("list the sushi restaurants", tenant="MHB")
    assert "couldn't find any restaurants" in reply
    assert not _names_in(reply, MHB_NAMES | MGV_NAMES)
    assert "cuisine" in reply.lower()
    assert browse_reply_violations(reply) == []


@pytest.mark.asyncio
async def test_a_provider_error_is_reported_and_never_replaced_by_knowledge_text() -> None:
    class Failing(FakeDiningTools):
        async def list_venues(self, **_kw):
            raise DiningToolError("boom", vendor_code="INTERNAL", http_status=500)

    result, reply = await _ask("what are the dining venues", dining=Failing())
    assert "couldn't retrieve" in reply
    assert not _names_in(reply, MGV_NAMES | MHB_NAMES)
    assert not result.get("retrieved") and "allergen" not in reply.lower()


@pytest.mark.asyncio
async def test_long_lists_are_capped_at_ten_with_a_count_of_the_rest() -> None:
    template = next(v for v in FAKE_VENUES if v.tenant_id == "MGV")
    many = [template.model_copy(update={"venue_id": f"v{i}", "name": f"Venue Number {i:02d}"}) for i in range(13)]
    _result, reply = await _ask("list restaurants", dining=FakeDiningTools(venues=many))
    assert sum(1 for i in range(13) if f"Venue Number {i:02d}" in reply) == 10
    assert "and 3 more" in reply


@pytest.mark.asyncio
async def test_a_browse_reply_never_claims_anything_was_booked() -> None:
    _result, reply = await _ask("what are the dining venues available in nevada")
    for word in ("confirmed", "booked", "held", "all set"):
        assert word not in reply.lower()


def test_the_forbidden_content_checker_catches_what_it_should() -> None:
    assert browse_reply_violations("Sakura is safe for a peanut allergy") != []
    assert browse_reply_violations("All set — your table is booked") != []
    assert browse_reply_violations("It's confirmed") != []
    assert browse_reply_violations("Here are the restaurants at Meridian Grand:\n• Sakura Sushi Bar — japanese, $$$") == []


@pytest.mark.asyncio
async def test_a_browse_plus_book_message_shows_the_list_and_books_the_named_venue() -> None:
    """Spec edge case "a browse message that also books" (the comma form; the bare-"and" form is the deferred R-12
    case): the browse is answered and the booking part proceeds."""
    dining = FakeDiningTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.GAMING: lambda t, g: FakeGamingTools()}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, classifier=UnavailableClassifier())
    result = await app.ainvoke(
        new_session_state(
            tenant_id="MGV", guest_id="g1", text="show me the restaurants, and book the steakhouse for 2 tomorrow at 7pm"
        ),
        config=thread_config("browse-plus-book"),
    )
    replies_text = "\n".join(str(m.content) for m in result["messages"] if m.type == "ai")
    assert "Here are the restaurants at Meridian Grand" in replies_text
    assert len(result["confirmations"]) == 1 and result["confirmations"][0].domain == Domain.DINING
