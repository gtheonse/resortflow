"""Stay's `SagaParticipant` (ADR-061)."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from agents._shared.pipeline import run_propose_and_hold
from agents.stay.graph import compile_stay_graph
from agents.stay.nodes.confirm import make_confirm_node
from agents.stay.nodes.hold_placement import make_hold_placement_node
from agents.stay.nodes.intent_parser import RuleBasedIntentExtractor, make_intent_parser_node
from agents.stay.nodes.room_discovery import make_room_discovery_node, offer_id_from_proposal_id
from agents.stay.tools import McpStayTools, StayToolError
from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, Proposal
from graph.participants import ConfirmContext
from graph.state import ResortFlowState


class StayParticipant:
    domain = Domain.STAY
    commit_rank = 4  # a room is rarely the scarce resource: the most flexible commits last

    def tools(self, tenant_id: str, guest_id: str) -> McpStayTools:
        return McpStayTools(tenant_id=tenant_id, guest_id=guest_id)

    def compile(self, tools: Any, checkpointer: BaseCheckpointSaver[Any] | None) -> Any:
        return compile_stay_graph(tools, trace=False, checkpointer=checkpointer)

    async def propose_and_hold(self, state: ResortFlowState, tools: Any, ledger: HoldLedger) -> dict[str, Any]:
        """No separate availability step: `search_rooms` already excludes sold-out offers."""

        async def body(working: dict[str, Any], accumulated: dict[str, Any], apply: Any) -> dict[str, Any] | None:
            apply(make_intent_parser_node(RuleBasedIntentExtractor())(working))
            apply(await make_room_discovery_node(tools)(working))
            if not working.get("selected_proposal_id"):
                return None
            apply(await make_hold_placement_node(tools, ledger)(working))
            return None

        return await run_propose_and_hold(
            dict(state), domain=self.domain, error_type=StayToolError, body=body
        )

    async def confirm(self, tools: Any, ledger: HoldLedger, ctx: ConfirmContext) -> dict[str, Any]:
        result: dict[str, Any] = await make_confirm_node(tools, ledger)(ctx.as_state())
        return result

    async def release(self, tools: Any, hold: Hold) -> None:
        await tools.release_room_hold(hold_id=hold.hold_id, idempotency_key=f"{hold.idempotency_key}:release")

    async def find_alternative(self, tools: Any, failed: Proposal | None) -> Proposal | None:
        """A different room offer for the same window, excluding the one that failed."""
        if failed is None or failed.starts_at is None or failed.ends_at is None:
            return None
        failed_offer_id = offer_id_from_proposal_id(failed.proposal_id)
        try:
            offers = await tools.search_rooms(
                arrival=failed.starts_at.date().isoformat(),
                departure=failed.ends_at.date().isoformat(),
                adults=failed.party_size or 2,
            )
        except StayToolError:
            return None
        candidate = next((o for o in offers if o.OFFER_ID != failed_offer_id and o.AVAILABLE_UNITS > 0), None)
        if candidate is None:
            return None
        return failed.model_copy(update={
            "proposal_id": f"{failed.proposal_id}:alt",
            "title": candidate.ROOM_DESCRIPTION,
            "hold_idempotency_key": None,
        })

    async def verify(self, tools: Any, confirmation: Confirmation) -> bool | None:
        return None  # the stay gateway publishes no status-check tool (disclosed gap)
