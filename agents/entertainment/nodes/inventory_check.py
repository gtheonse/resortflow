"""T026 — Inventory check: MCP `check_inventory`, picks a `Section` and
refines the selected `Proposal`'s `starts_at`/`ends_at`/`price`. Mirrors
`agents/dining/nodes/availability.py`'s check-then-refine pattern, but
entertainment has no waitlist equivalent (`entertainment-mcp-tool-
contract.md` defines no such tool) — no section with enough seats is a
dead end reported in a message, routed to END by
`edges.conditions.route_after_inventory_check`, never `HOLD_PLACEMENT`.

`get_event_detail` is called here (not carried forward from
`event_discovery`'s `search_events` response) to obtain `start_utc`/
`duration_minutes` — `graph.models.Proposal` has no dedicated field to smuggle
an event's raw vendor timing fields through untouched, so this node re-fetches
them the same way `agents/dining/nodes/availability.py` re-fetches slot data
via a dedicated MCP call rather than trusting anything carried over from
discovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from gateway.entertainment.models import Section
from langchain_core.messages import AIMessage

from agents.entertainment.nodes.event_discovery import event_id_from_proposal_id
from agents.entertainment.state import EntertainmentState
from agents.entertainment.tools import EntertainmentToolError, EntertainmentTools
from graph.errors import classify
from graph.models import Domain, Proposal, ToolFailure


def _pick_section(sections: list[Section], quantity: int) -> Section | None:
    """Cheapest section with enough available seats for `quantity`. `None`
    when no section qualifies — the no-availability dead end."""
    candidates = [s for s in sections if s.available >= quantity]
    if not candidates:
        return None
    return min(candidates, key=lambda s: s.price_per_seat)


def make_inventory_check_node(tools: EntertainmentTools) -> Any:
    def _find_proposal(state: EntertainmentState) -> Proposal | None:
        selected_id = state.get("selected_proposal_id")
        if not selected_id:
            return None
        for proposal in state.get("proposals", []):
            if proposal.proposal_id == selected_id:
                return proposal
        return None

    async def inventory_check(state: EntertainmentState) -> dict[str, Any]:
        proposal = _find_proposal(state)
        if proposal is None:
            return {}

        intent = state.get("intent") or {}
        event_id = event_id_from_proposal_id(proposal.proposal_id)
        quantity = proposal.party_size or intent.get("quantity") or 1

        try:
            inventory = await tools.check_inventory(event_id=event_id)
        except EntertainmentToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.ENTERTAINMENT,
                tool="check_inventory",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            return {
                "failures": [failure],
                "messages": [AIMessage(content="Inventory could not be checked for that event.")],
            }

        section = _pick_section(inventory.sections, quantity)
        if section is None:
            return {
                "messages": [
                    AIMessage(
                        content=(
                            f"No section has {quantity} seats available for that event — "
                            "no availability."
                        )
                    )
                ]
            }

        try:
            detail = await tools.get_event_detail(event_id=event_id)
        except EntertainmentToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.ENTERTAINMENT,
                tool="get_event_detail",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            return {
                "failures": [failure],
                "messages": [AIMessage(content="Event detail could not be retrieved.")],
            }

        starts_at = datetime.fromisoformat(detail.start_utc)
        ends_at = starts_at + timedelta(minutes=detail.duration_minutes)
        price = Decimal(str(section.price_per_seat + section.fees_per_seat))

        updated_proposal = proposal.model_copy(
            update={"starts_at": starts_at, "ends_at": ends_at, "price": price}
        )
        return {
            "proposals": [updated_proposal],
            "selected_section_id": section.section_id,
        }

    return inventory_check

