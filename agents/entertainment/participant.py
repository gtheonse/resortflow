"""Entertainment's `SagaParticipant` (ADR-061)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from agents._shared.pipeline import run_propose_and_hold, selected_proposal
from agents.entertainment.graph import compile_entertainment_graph
from agents.entertainment.nodes.confirm import make_confirm_node
from agents.entertainment.nodes.event_discovery import (
    event_id_from_proposal_id,
    make_event_discovery_node,
)
from agents.entertainment.nodes.hold_placement import make_hold_placement_node
from agents.entertainment.nodes.intent_parser import (
    RuleBasedIntentExtractor,
    make_intent_parser_node,
)
from agents.entertainment.nodes.inventory_check import make_inventory_check_node
from agents.entertainment.tools import EntertainmentToolError, McpEntertainmentTools
from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, Proposal
from graph.participants import ConfirmContext
from graph.state import ResortFlowState


class EntertainmentParticipant:
    domain = Domain.ENTERTAINMENT
    commit_rank = 1  # fixed showtimes, no reschedule: the scarcest resource commits first

    def tools(self, tenant_id: str, guest_id: str) -> McpEntertainmentTools:
        return McpEntertainmentTools(tenant_id=tenant_id, guest_id=guest_id)

    def compile(self, tools: Any, checkpointer: BaseCheckpointSaver[Any] | None) -> Any:
        return compile_entertainment_graph(tools, trace=False, checkpointer=checkpointer)

    async def propose_and_hold(self, state: ResortFlowState, tools: Any, ledger: HoldLedger) -> dict[str, Any]:
        async def body(working: dict[str, Any], accumulated: dict[str, Any], apply: Any) -> dict[str, Any] | None:
            apply(make_intent_parser_node(RuleBasedIntentExtractor())(working))
            apply(await make_event_discovery_node(tools)(working))
            if not working.get("selected_proposal_id"):
                return None
            apply(await make_inventory_check_node(tools)(working))
            proposal = selected_proposal(working)
            if proposal is None or proposal.starts_at is None:
                return None
            apply(await make_hold_placement_node(tools, ledger)(working))
            return None

        return await run_propose_and_hold(
            dict(state), domain=self.domain, error_type=EntertainmentToolError, body=body
        )

    async def confirm(self, tools: Any, ledger: HoldLedger, ctx: ConfirmContext) -> dict[str, Any]:
        result: dict[str, Any] = await make_confirm_node(tools, ledger)(ctx.as_state())
        return result

    async def release(self, tools: Any, hold: Hold) -> None:
        await tools.release_seat_hold(hold_id=hold.hold_id, idempotency_key=f"{hold.idempotency_key}:release")

    async def find_alternative(self, tools: Any, failed: Proposal | None) -> Proposal | None:
        """A different event (never a reschedule — fixed showtimes), excluding the one that failed."""
        if failed is None:
            return None
        failed_event_id = event_id_from_proposal_id(failed.proposal_id)
        try:
            response = await tools.search_events()
        except EntertainmentToolError:
            return None
        candidate = next((e for e in response.events if e.event_id != failed_event_id), None)
        if candidate is None:
            return None
        starts_at = datetime.fromisoformat(candidate.start_utc)
        return failed.model_copy(update={
            "proposal_id": f"{failed.proposal_id}:alt",
            "title": candidate.name,
            "starts_at": starts_at,
            "ends_at": starts_at + timedelta(minutes=candidate.duration_minutes),
            "location_zone": (candidate.venue or {}).get("zone") or failed.location_zone,
            "hold_idempotency_key": None,
        })

    async def verify(self, tools: Any, confirmation: Confirmation) -> bool | None:
        """Here `Confirmation.confirmation_id` is the vendor's `order_id`, so a direct
        `get_order_status` round-trip is possible. `pending` stays verified (the real
        vendor resolves async via webhook, never modelled); a transient vendor error is
        inconclusive and never manufactures a false mismatch."""
        try:
            status = await tools.get_order_status(order_id=confirmation.confirmation_id)
        except EntertainmentToolError:
            return True
        if status.order is None:
            return False
        return status.order.status not in ("failed", "cancelled")
