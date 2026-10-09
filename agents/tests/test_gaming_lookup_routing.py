"""A gaming-only lookup ("what's my loyalty tier") goes through the gated gaming graph, not booking lookup.

Found by the gaming adversarial suite (failing since feature 010, confirmed identical on the pre-011 commit): the
`LOOKUP -> BOOKING_LOOKUP` branch in `_route_from_intent_router` came before the gaming-only branch its own comment
describes, so a self-excluded guest asking about loyalty got "I couldn't find a booking" instead of the jurisdiction's
mandated message (Constitution IV: mandated messaging is appended deterministically)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import thread_config
from graph.models import Domain, RequestKind
from graph.routing.classifier import UnavailableClassifier
from graph.supervisor import compile_supervisor_graph, new_session_state

LOYALTY = ["what's my loyalty tier", "how many comp dollars do I have from my loyalty account", "what's my casino loyalty status"]


async def _ask(text: str, guest: str):
    gaming = FakeGamingTools()
    app = compile_supervisor_graph(
        checkpointer=MemorySaver(), tools_factory={Domain.GAMING: lambda t, g: gaming}, classifier=UnavailableClassifier()
    )
    result = await app.ainvoke(
        new_session_state(tenant_id="MGV", guest_id=guest, text=text), config=thread_config(f"gl-{guest}-{abs(hash(text))}")
    )
    return result, gaming, "\n".join(str(m.content) for m in result["messages"] if m.type == "ai")


@pytest.mark.asyncio
@pytest.mark.parametrize("text", LOYALTY)
async def test_a_self_excluded_guest_gets_the_mandated_message_not_a_booking_miss(text: str) -> None:
    result, gaming, reply = await _ask(text, "guest-self-excluded")
    assert result["routing"].kind == RequestKind.LOOKUP and result["routing"].services == [Domain.GAMING]
    assert "1-800-522-4700" in reply and "couldn't find a booking" not in reply
    assert set(gaming.calls) == {"check_rg_status"}  # the gate only: no loyalty read for an excluded guest


@pytest.mark.asyncio
async def test_a_cleared_guest_gets_their_loyalty_status() -> None:
    _result, gaming, reply = await _ask("what's my loyalty tier", "guest-good-standing")
    assert "gold" in reply.lower()
    assert gaming.calls.index("check_rg_status") < gaming.calls.index("get_loyalty_status")


@pytest.mark.asyncio
async def test_a_booking_lookup_is_unaffected() -> None:
    result, _gaming, _reply = await _ask("what have I booked", "guest-good-standing")
    assert result["routing"].kind == RequestKind.LOOKUP and not result["routing"].services
