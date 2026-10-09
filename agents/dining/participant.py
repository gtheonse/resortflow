"""Dining's `SagaParticipant` (ADR-061): everything the supervisor used to know
about dining — node pipeline, vendor release verb, recovery search — lives here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from agents._shared.pipeline import run_propose_and_hold, selected_proposal
from agents.dining.graph import compile_dining_graph
from agents.dining.nodes.allergen_check import (
    block_policy_flags,
    block_saga_event,
    evaluate_allergens,
)
from agents.dining.nodes.availability import make_availability_node
from agents.dining.nodes.confirm import make_confirm_node
from agents.dining.nodes.hold_placement import make_hold_placement_node
from agents.dining.nodes.intent_parser import RuleBasedIntentExtractor, make_intent_parser_node
from agents.dining.nodes.venue_discovery import make_venue_discovery_node, venue_id_from_proposal_id
from agents.dining.tools import DiningToolError, McpDiningTools
from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, Proposal
from graph.participants import ConfirmContext
from graph.state import ResortFlowState

_BLOCK_REPLY = (
    "I can't include the dining part of this request yet — a dietary restriction you "
    "mentioned isn't reflected in it. The rest of your request is unaffected."
)


class DiningParticipant:
    domain = Domain.DINING
    commit_rank = 2

    def tools(self, tenant_id: str, guest_id: str) -> McpDiningTools:
        return McpDiningTools(tenant_id=tenant_id, guest_id=guest_id)

    def compile(self, tools: Any, checkpointer: BaseCheckpointSaver[Any] | None) -> Any:
        return compile_dining_graph(tools, trace=False, checkpointer=checkpointer)

    async def propose_and_hold(self, state: ResortFlowState, tools: Any, ledger: HoldLedger) -> dict[str, Any]:
        """The propose-and-hold *prefix* of the standalone dining graph. The
        allergen decision is the same `evaluate_allergens` the standalone
        `allergen_check` node uses, run before any hold is placed (ADR-029/061)."""

        async def body(working: dict[str, Any], accumulated: dict[str, Any], apply: Any) -> dict[str, Any] | None:
            apply(make_intent_parser_node(RuleBasedIntentExtractor())(working))
            apply(await make_venue_discovery_node(tools)(working))
            if not working.get("selected_proposal_id"):
                return None
            proposal = selected_proposal(working)
            if proposal is not None:
                verdict = evaluate_allergens(working, proposal)
                if verdict.blocking:
                    # No hold is placed, so this proposal never reaches
                    # `HoldStatus.HELD` and `proposal_assembly`'s `held` filter
                    # excludes it from the cross-domain approval gate.
                    sequence = len(working.get("saga_log") or []) + 1
                    return {
                        **accumulated,
                        "messages": [AIMessage(content=_BLOCK_REPLY)],
                        "policy_flags": block_policy_flags(working.get("jurisdiction")),
                        "saga_log": [block_saga_event(working["saga_id"], sequence, proposal, verdict)],
                    }
            apply(await make_availability_node(tools)(working))
            if working.get("waitlisted"):
                return None
            proposal = selected_proposal(working)
            if proposal is None or proposal.starts_at is None:
                return None
            apply(await make_hold_placement_node(tools, ledger)(working))
            return None

        return await run_propose_and_hold(
            dict(state), domain=self.domain, error_type=DiningToolError, body=body
        )

    async def confirm(self, tools: Any, ledger: HoldLedger, ctx: ConfirmContext) -> dict[str, Any]:
        result: dict[str, Any] = await make_confirm_node(tools, ledger)(ctx.as_state())
        return result

    async def release(self, tools: Any, hold: Hold) -> None:
        await tools.release_hold(hold_id=hold.hold_id, idempotency_key=f"{hold.idempotency_key}:release")

    async def find_alternative(self, tools: Any, failed: Proposal | None) -> Proposal | None:
        if failed is None:
            return None
        venue_id = venue_id_from_proposal_id(failed.proposal_id)
        nearby_date = (
            (failed.starts_at + timedelta(days=1)).date().isoformat()
            if failed.starts_at else datetime.now(UTC).date().isoformat()
        )
        try:
            availability = await tools.check_availability(
                venue_id=venue_id, date=nearby_date, party_size=failed.party_size or 2,
            )
        except DiningToolError:
            return None
        if not availability.slots:
            return None
        alt_starts_at = datetime.fromisoformat(f"{nearby_date}T{availability.slots[0].time}:00")
        return failed.model_copy(update={
            "proposal_id": f"{failed.proposal_id}:alt",
            "starts_at": alt_starts_at,
            "ends_at": alt_starts_at + timedelta(minutes=90),
            "hold_idempotency_key": None,
        })

    async def verify(self, tools: Any, confirmation: Confirmation) -> bool | None:
        # `Confirmation.confirmation_id` is the guest-facing code, not the vendor's
        # `reservation_id`; widening the frozen model is an ADR-worthy change.
        return None
