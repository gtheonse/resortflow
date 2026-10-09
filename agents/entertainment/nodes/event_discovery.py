"""T026 — Event discovery: MCP `search_events`, filtered by the parsed
intent, emitted as `graph.models.Proposal` objects (domain=
`Domain.ENTERTAINMENT`) merged into `state["proposals"]` via the frozen
`merge_proposals` reducer. Mirrors `agents/dining/nodes/venue_discovery.py`.

`tenant_id` on every `Proposal` comes from `state["tenant_id"]` (itself set
once at conversation entry from a verified token claim) — never from the
LLM-parsed intent, never from the vendor response body.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from agents.entertainment.state import EntertainmentState
from agents.entertainment.tools import EntertainmentTools
from graph.models import Domain, Proposal

MAX_CANDIDATES = 5


def _proposal_id(session_id: str, event_id: str) -> str:
    """Deterministic — identical on every resume/replay of the same
    session, which is what `merge_proposals`'s per-`proposal_id` merge
    depends on to stay idempotent."""
    return f"entertainment:{session_id}:{event_id}"


def event_id_from_proposal_id(proposal_id: str) -> str:
    """Inverse of `_proposal_id`. `Proposal` has no dedicated `event_id`
    field (frozen model — data-model.md), so the event id is carried in the
    deterministic `proposal_id` itself rather than added as an extra field
    downstream nodes would have to invent."""
    return proposal_id.rsplit(":", 1)[-1]


def make_event_discovery_node(tools: EntertainmentTools) -> Any:
    async def event_discovery(state: EntertainmentState) -> dict[str, Any]:
        intent = state.get("intent") or {}
        tenant_id = state["tenant_id"]
        session_id = state["session_id"]
        quantity = intent.get("quantity")

        response = await tools.search_events(
            classification=intent.get("classification"), date=intent.get("date_str")
        )

        candidates = list(response.events)[:MAX_CANDIDATES]

        proposals = [
            Proposal(
                proposal_id=_proposal_id(session_id, event.event_id),
                domain=Domain.ENTERTAINMENT,
                tenant_id=tenant_id,
                title=event.name,
                location_zone=(event.venue or {}).get("zone"),
                party_size=quantity,
            )
            for event in candidates
        ]

        update: dict[str, Any] = {"proposals": proposals}
        if not proposals:
            update["messages"] = [
                AIMessage(
                    content=(
                        "No events matched that request — try broadening the "
                        "classification, date, or number of tickets."
                    )
                )
            ]
            update["selected_proposal_id"] = None
        else:
            update["selected_proposal_id"] = proposals[0].proposal_id
        return update

    return event_discovery
