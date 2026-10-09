"""T047 (stay) — Room discovery: MCP `search_rooms`, filtered by the parsed
intent, emitted as `graph.models.Proposal` objects (domain=`Domain.STAY`)
merged into `state["proposals"]` via the frozen `merge_proposals` reducer.

Unlike dining/wellness, stay has no separate availability step: `search_rooms`
already returns only offers with `AVAILABLE_UNITS > 0` for the requested
arrival/departure/occupancy (see `sims/pms/main.py`'s `search_rooms`, which
excludes offers with no remaining inventory after subtracting active holds),
so this single node is both discovery and the availability check — the
5-node graph shape (`intent_parser -> room_discovery -> hold_placement ->
human_approval -> confirm`) has no dedicated `AVAILABILITY` node for that
reason. No rooms found routes straight to END via `route_after_room_discovery`
(see `agents/stay/edges/conditions.py`) rather than a waitlist — stay has no
waitlist equivalent, same disclosed gap wellness's `availability.py` notes
for its own domain.

`tenant_id` on every `Proposal` comes from `state["tenant_id"]` (itself set
once at conversation entry from a verified token claim) — never from the
LLM-parsed intent, never from the vendor response body.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage

from agents.stay.state import StayState
from agents.stay.tools import StayTools
from graph.models import Domain, Proposal

MAX_CANDIDATES = 5

LOCATION_ZONE = "main_tower"
"""Stay has no per-offer `location_zone` concept the way dining/wellness do
(`RoomOffer` carries no zone field — `mcp/gateway/stay/models.py`). A
constant zone is used for every stay `Proposal` instead of inventing one:
`"main_tower"` matches the zone `sims/pms/main.py`'s own
`confirm_room_reservation` hardcodes onto every new `stays` row, and is one
of `graph/travel_time.py`'s known MGV/MHB zones, so the cross-domain
travel-time/conflict checker can still resolve a distance for it."""


def _proposal_id(session_id: str, offer_id: str) -> str:
    """Deterministic — identical on every resume/replay of the same
    session, which is what `merge_proposals`'s per-`proposal_id` merge
    depends on to stay idempotent."""
    return f"stay:{session_id}:{offer_id}"


def offer_id_from_proposal_id(proposal_id: str) -> str:
    """Inverse of `_proposal_id`. `Proposal` has no dedicated `offer_id`
    field (frozen model — data-model.md), so the offer id is carried in the
    deterministic `proposal_id` itself rather than added as an extra field
    downstream nodes would have to invent."""
    return proposal_id.rsplit(":", 1)[-1]


def make_room_discovery_node(tools: StayTools, *, today: date | None = None) -> Any:
    async def room_discovery(state: StayState) -> dict[str, Any]:
        intent = state.get("intent") or {}
        tenant_id = state["tenant_id"]
        session_id = state["session_id"]
        party_size = intent.get("party_size")
        adults = party_size or 2
        nights = intent.get("nights") or 1

        resolved_today = today or date.today()  # noqa: DTZ011 — local wall-clock date is correct here
        arrival_defaulted = not intent.get("date_str")
        date_str = intent.get("date_str") or (resolved_today + timedelta(days=1)).isoformat()
        arrival_date = date.fromisoformat(date_str)
        departure_date = arrival_date + timedelta(days=nights)

        # Found live (demo report): this default was only ever applied
        # locally, never written back into `state["intent"]` — so
        # `hold_placement`'s own later `intent.get("date_str")` re-read the
        # original (still-`None`) value and silently gave up (`return {}`,
        # no hold, no failure, no message) even though rooms had just been
        # found for exactly this defaulted date. Persisting it here is what
        # makes `hold_placement` see the same date `room_discovery` used.
        intent = {**intent, "date_str": date_str, "arrival_assumed": arrival_defaulted}

        offers = await tools.search_rooms(
            arrival=date_str,
            departure=departure_date.isoformat(),
            adults=adults,
            room_class=intent.get("room_class"),
        )
        candidates = offers[:MAX_CANDIDATES]

        proposals = [
            Proposal(
                proposal_id=_proposal_id(session_id, offer.OFFER_ID),
                domain=Domain.STAY,
                tenant_id=tenant_id,
                title=offer.ROOM_DESCRIPTION,
                location_zone=LOCATION_ZONE,
                party_size=party_size,
                price=Decimal(str(offer.NIGHTLY_RATES[0].AMOUNT)) if offer.NIGHTLY_RATES else None,
            )
            for offer in candidates
        ]

        update: dict[str, Any] = {"proposals": proposals, "intent": intent}
        if not proposals:
            update["messages"] = [
                AIMessage(
                    content=(
                        "No rooms matched that request — try a different room class, "
                        "date, or occupancy."
                    )
                )
            ]
            update["selected_proposal_id"] = None
        else:
            update["selected_proposal_id"] = proposals[0].proposal_id
        return update

    return room_discovery
