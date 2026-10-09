"""T047 — Venue discovery: MCP `list_venues`, filtered by the parsed
intent, emitted as `graph.models.Proposal` objects (domain=`Domain.DINING`)
merged into `state["proposals"]` via the frozen `merge_proposals` reducer.

`tenant_id` on every `Proposal` comes from `state["tenant_id"]` (itself set
once at conversation entry from a verified token claim) — never from the
LLM-parsed intent, never from the vendor response body.
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage

from agents.dining.state import DiningState
from agents.dining.tools import DiningTools
from graph.models import Domain, Proposal

MAX_CANDIDATES = 5

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _named_in_text(text: str, venues: list) -> list:
    """Feature 011 (SC-006): the venues whose full name the guest wrote ("book Sakura Sushi Bar ..."), compared
    as whole-word sequences so case and punctuation do not matter. Names come from the tenant's own venue list,
    never from the message — the text only selects among what the provider returned."""
    haystack = _words(text)
    hits = []
    for venue in venues:
        needle = _words(venue.name)
        if needle and any(haystack[i : i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1)):
            hits.append(venue)
    return hits


def _proposal_id(session_id: str, venue_id: str) -> str:
    """Deterministic — identical on every resume/replay of the same
    session, which is what `merge_proposals`'s per-`proposal_id` merge
    depends on to stay idempotent."""
    return f"dining:{session_id}:{venue_id}"


def venue_id_from_proposal_id(proposal_id: str) -> str:
    """Inverse of `_proposal_id`. `Proposal` has no dedicated `venue_id`
    field (frozen model — data-model.md), so the venue id is carried in the
    deterministic `proposal_id` itself rather than added as an extra field
    downstream nodes would have to invent."""
    return proposal_id.rsplit(":", 1)[-1]


def make_venue_discovery_node(tools: DiningTools):
    async def venue_discovery(state: DiningState) -> dict:
        intent = state.get("intent") or {}
        tenant_id = state["tenant_id"]
        session_id = state["session_id"]
        party_size = intent.get("party_size")

        # `supports_dietary` filtering is deliberately not applied here — the
        # allergen_check node performs the authoritative, policy-critical
        # cross-check against actual menu content. Pre-filtering venues by a
        # vendor-reported "supports_dietary" flag would let a venue that
        # merely *claims* support skip that check.
        response = await tools.list_venues(cuisine=intent.get("cuisine"))
        venues = list(response.venues)

        # Feature 011 (SC-006): a guest who names the venue gets THAT venue. A name can contain a cuisine word
        # ("Sakura Sushi Bar" -> cuisine "sushi", which no venue has), so when the cuisine-filtered list holds no
        # named venue, one unfiltered read looks for it; the name selects only among this tenant's own venues.
        raw_text = intent.get("raw_text") or ""
        named = _named_in_text(raw_text, venues) if raw_text else []
        if raw_text and not named and intent.get("cuisine"):
            named = _named_in_text(raw_text, list((await tools.list_venues(cuisine=None)).venues))
        if named:
            venues = named

        candidates = [
            venue
            for venue in venues
            if party_size is None or (venue.min_party <= party_size <= venue.max_party)
        ][:MAX_CANDIDATES]

        dietary = intent.get("dietary") or []
        proposals = [
            Proposal(
                proposal_id=_proposal_id(session_id, venue.venue_id),
                domain=Domain.DINING,
                tenant_id=tenant_id,
                title=venue.name,
                location_zone=venue.location_zone,
                party_size=party_size,
                dietary=list(dietary),
            )
            for venue in candidates
        ]

        update: dict = {"proposals": proposals}
        if not proposals:
            update["messages"] = [
                AIMessage(
                    content=(
                        "No venues matched that request — try broadening the cuisine, "
                        "party size, or dietary filter."
                    )
                )
            ]
            update["selected_proposal_id"] = None
        else:
            update["selected_proposal_id"] = proposals[0].proposal_id
        return update

    return venue_discovery
