"""Feature 011, SC-006 — a booking that names the venue books THAT venue.

Found live: "book Sakura Sushi Bar for 2 tomorrow at 7pm" extracted the cuisine "sushi" from the venue's name,
found no venue with that cuisine ("japanese") and said nothing matched; and a name with no cuisine keyword
("book Cactus Flower Cantina") listed every venue and proposed the first one — the wrong restaurant."""

from __future__ import annotations

import pytest

from agents.dining.nodes.venue_discovery import make_venue_discovery_node, venue_id_from_proposal_id
from agents.dining.tests.fakes import FakeDiningTools


async def _discover(text: str, *, cuisine: str | None = None, party: int | None = 2, tenant: str = "MGV"):
    node = make_venue_discovery_node(FakeDiningTools(tenant_id=tenant))
    state = {
        "tenant_id": tenant,
        "session_id": "s1",
        "intent": {"raw_text": text, "cuisine": cuisine, "party_size": party},
    }
    return await node(state)


def _venue_ids(update) -> list[str]:
    return [venue_id_from_proposal_id(p.proposal_id) for p in update["proposals"]]


@pytest.mark.asyncio
async def test_a_name_containing_a_cuisine_word_resolves_to_that_venue() -> None:
    update = await _discover("book Sakura Sushi Bar for 2 tomorrow at 7pm", cuisine="sushi")
    assert _venue_ids(update) == ["ven-mgv-02"]
    assert venue_id_from_proposal_id(update["selected_proposal_id"]) == "ven-mgv-02"


@pytest.mark.asyncio
async def test_a_name_with_no_cuisine_word_is_not_replaced_by_the_first_venue() -> None:
    update = await _discover("book Cactus Flower Cantina for 2")
    assert _venue_ids(update) == ["ven-mgv-03"]


@pytest.mark.asyncio
async def test_matching_ignores_case_and_punctuation() -> None:
    update = await _discover("reserve the GREEN table, please", party=2)
    assert _venue_ids(update) == ["ven-mgv-04"]


@pytest.mark.asyncio
async def test_a_cuisine_request_without_a_name_behaves_as_before() -> None:
    update = await _discover("book a sushi place for 2", cuisine="sushi")
    assert update["proposals"] == []
    assert "No venues matched" in update["messages"][0].content
    mexican = await _discover("book a mexican place for 2", cuisine="mexican")
    assert _venue_ids(mexican) == ["ven-mgv-03"]


@pytest.mark.asyncio
async def test_a_named_venue_still_respects_the_party_size_limits() -> None:
    update = await _discover("book Meridian Steakhouse for 1", party=1)  # min_party is 2
    assert update["proposals"] == []


@pytest.mark.asyncio
async def test_another_tenants_venue_name_never_resolves() -> None:
    update = await _discover("book Harbor Chophouse for 2", tenant="MGV")
    assert update["proposals"] == [] or "ven-mhb-01" not in _venue_ids(update)
