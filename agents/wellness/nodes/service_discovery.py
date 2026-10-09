"""T030 — Service discovery: MCP `list_services`, filtered by the parsed
intent, emitted as `graph.models.Proposal` objects (domain=`Domain.WELLNESS`)
merged into `state["proposals"]` via the frozen `merge_proposals` reducer.

`tenant_id` on every `Proposal` comes from `state["tenant_id"]` (itself set
once at conversation entry from a verified token claim) — never from the
LLM-parsed intent, never from the vendor response body.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from agents.wellness.state import WellnessState
from agents.wellness.tools import WellnessTools
from graph.models import Domain, Proposal

MAX_CANDIDATES = 5


def _proposal_id(session_id: str, service_id: str) -> str:
    """Deterministic — identical on every resume/replay of the same
    session, which is what `merge_proposals`'s per-`proposal_id` merge
    depends on to stay idempotent."""
    return f"wellness:{session_id}:{service_id}"


def service_id_from_proposal_id(proposal_id: str) -> str:
    """Inverse of `_proposal_id`. `Proposal` has no dedicated `service_id`
    field (frozen model — data-model.md), so the service id is carried in
    the deterministic `proposal_id` itself rather than added as an extra
    field downstream nodes would have to invent."""
    return proposal_id.rsplit(":", 1)[-1]


def make_service_discovery_node(tools: WellnessTools) -> Any:
    async def service_discovery(state: WellnessState) -> dict[str, Any]:
        intent = state.get("intent") or {}
        tenant_id = state["tenant_id"]
        session_id = state["session_id"]
        party_size = intent.get("party_size")

        response = await tools.list_services(category=intent.get("category"))
        candidates = response.services[:MAX_CANDIDATES]

        proposals = [
            Proposal(
                proposal_id=_proposal_id(session_id, service.service_id),
                domain=Domain.WELLNESS,
                tenant_id=tenant_id,
                title=service.name,
                location_zone=service.location_zone,
                party_size=party_size,
            )
            for service in candidates
        ]

        update: dict[str, Any] = {"proposals": proposals}
        if not proposals:
            update["messages"] = [
                AIMessage(
                    content=(
                        "No wellness services matched that request — try broadening the "
                        "category or date."
                    )
                )
            ]
            update["selected_proposal_id"] = None
        else:
            update["selected_proposal_id"] = proposals[0].proposal_id
        return update

    return service_discovery
