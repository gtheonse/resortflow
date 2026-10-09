"""Wellness's `SagaParticipant` (ADR-061)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from agents._shared.pipeline import run_propose_and_hold, selected_proposal
from agents.wellness.graph import compile_wellness_graph
from agents.wellness.nodes.availability import make_availability_node
from agents.wellness.nodes.confirm import make_confirm_node
from agents.wellness.nodes.hold_placement import make_hold_placement_node
from agents.wellness.nodes.intent_parser import RuleBasedIntentExtractor, make_intent_parser_node
from agents.wellness.nodes.service_discovery import (
    make_service_discovery_node,
    service_id_from_proposal_id,
)
from agents.wellness.tools import McpWellnessTools, WellnessToolError
from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, Proposal
from graph.participants import ConfirmContext
from graph.state import ResortFlowState


class WellnessParticipant:
    domain = Domain.WELLNESS
    commit_rank = 3

    def tools(self, tenant_id: str, guest_id: str) -> McpWellnessTools:
        return McpWellnessTools(tenant_id=tenant_id, guest_id=guest_id)

    def compile(self, tools: Any, checkpointer: BaseCheckpointSaver[Any] | None) -> Any:
        return compile_wellness_graph(tools, trace=False, checkpointer=checkpointer)

    async def propose_and_hold(self, state: ResortFlowState, tools: Any, ledger: HoldLedger) -> dict[str, Any]:
        async def body(working: dict[str, Any], accumulated: dict[str, Any], apply: Any) -> dict[str, Any] | None:
            apply(make_intent_parser_node(RuleBasedIntentExtractor())(working))
            apply(await make_service_discovery_node(tools)(working))
            if not working.get("selected_proposal_id"):
                return None
            apply(await make_availability_node(tools)(working))
            proposal = selected_proposal(working)
            if proposal is None or proposal.starts_at is None:
                return None
            apply(await make_hold_placement_node(tools, ledger)(working))
            return None

        return await run_propose_and_hold(
            dict(state), domain=self.domain, error_type=WellnessToolError, body=body
        )

    async def confirm(self, tools: Any, ledger: HoldLedger, ctx: ConfirmContext) -> dict[str, Any]:
        result: dict[str, Any] = await make_confirm_node(tools, ledger)(ctx.as_state())
        return result

    async def release(self, tools: Any, hold: Hold) -> None:
        await tools.release_amenity_hold(hold_id=hold.hold_id, idempotency_key=f"{hold.idempotency_key}:release")

    async def find_alternative(self, tools: Any, failed: Proposal | None) -> Proposal | None:
        """Same service, next day, first open slot."""
        if failed is None:
            return None
        service_id = service_id_from_proposal_id(failed.proposal_id)
        nearby_date = (
            (failed.starts_at + timedelta(days=1)).date().isoformat()
            if failed.starts_at else datetime.now(UTC).date().isoformat()
        )
        try:
            availability = await tools.check_service_availability(service_id=service_id, date=nearby_date)
        except WellnessToolError:
            return None
        if not availability.slots:
            return None
        alt_starts_at = datetime.fromisoformat(f"{nearby_date}T{availability.slots[0].time}:00")
        duration = (
            failed.ends_at - failed.starts_at
            if failed.starts_at and failed.ends_at else timedelta(minutes=60)
        )
        return failed.model_copy(update={
            "proposal_id": f"{failed.proposal_id}:alt",
            "starts_at": alt_starts_at,
            "ends_at": alt_starts_at + duration,
            "hold_idempotency_key": None,
        })

    async def verify(self, tools: Any, confirmation: Confirmation) -> bool | None:
        return None  # the wellness gateway publishes no status-check tool (disclosed gap)
