"""T027 — Hold placement: MCP `hold_seats`, constructs `graph.models.Hold`
(status=`HoldStatus.HELD`, `expires_at` required) and merges it into
`state["holds"]` via `merge_holds`. Mirrors
`agents/dining/nodes/hold_placement.py` exactly. Keyed by `idempotency_key`
— `hold_id` is vendor-assigned and stays `None` in `graph.models.Hold` terms
only if the vendor call fails; on success the vendor's `hold_id` is
recorded too, purely as a display/debug field, never as the merge key.

Write-ahead ledger (`graph.ledger.HoldLedger`, ADR-019): `insert_pending` is
called *before* the vendor call, so a crash between here and the vendor
responding leaves a `PENDING` row a reaper can reconcile. `mark_held`/
`mark_released` record the outcome once known. The ledger is a side channel
to `state["holds"]`, not a replacement for it — `ResortFlowState` still owns the
conversation-visible `Hold`; the ledger owns proof of the external side
effect independent of the checkpointer (Constitution Principle V: these two
records must never be conflated).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.entertainment.nodes.event_discovery import event_id_from_proposal_id
from agents.entertainment.state import EntertainmentState
from agents.entertainment.tools import EntertainmentToolError, EntertainmentTools, idempotency_key
from graph.errors import classify
from graph.ledger import HoldLedger
from graph.models import Domain, Hold, HoldStatus, Proposal, ToolFailure


def make_hold_placement_node(tools: EntertainmentTools, ledger: HoldLedger) -> Any:
    async def hold_placement(state: EntertainmentState) -> dict[str, Any]:
        selected_id = state.get("selected_proposal_id")
        proposal: Proposal | None = None
        for candidate in state.get("proposals", []):
            if candidate.proposal_id == selected_id:
                proposal = candidate
                break
        section_id = state.get("selected_section_id")
        if proposal is None or proposal.starts_at is None or section_id is None:
            # No section selected (e.g. no availability) — nothing to hold.
            return {}

        attempt = state.get("hold_attempt", 0) + 1
        key = idempotency_key(state["session_id"], proposal.proposal_id, attempt)
        event_id = event_id_from_proposal_id(proposal.proposal_id)

        await ledger.insert_pending(
            saga_id=state["saga_id"],
            session_id=state["session_id"],
            tenant_id=state["tenant_id"],
            domain=Domain.ENTERTAINMENT,
            idempotency_key=key,
        )

        try:
            vendor_hold = await tools.hold_seats(
                event_id=event_id,
                section_id=section_id,
                quantity=proposal.party_size or 1,
                idempotency_key=key,
            )
        except EntertainmentToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.ENTERTAINMENT,
                tool="hold_seats",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            hold = Hold(
                idempotency_key=key,
                domain=Domain.ENTERTAINMENT,
                tenant_id=state["tenant_id"],
                saga_id=state["saga_id"],
                status=HoldStatus.RELEASED,
                proposal_id=proposal.proposal_id,
            )
            await ledger.mark_released(idempotency_key=key)
            return {"failures": [failure], "holds": [hold], "hold_attempt": attempt}

        expires_at = datetime.fromisoformat(vendor_hold.expires_at)
        await ledger.mark_held(idempotency_key=key, hold_id=vendor_hold.hold_id, expires_at=expires_at)
        hold = Hold(
            idempotency_key=key,
            hold_id=vendor_hold.hold_id,
            domain=Domain.ENTERTAINMENT,
            tenant_id=state["tenant_id"],
            saga_id=state["saga_id"],
            status=HoldStatus.HELD,
            expires_at=expires_at,
            proposal_id=proposal.proposal_id,
        )
        updated_proposal = proposal.model_copy(update={"hold_idempotency_key": key})
        return {
            "holds": [hold],
            "proposals": [updated_proposal],
            "hold_attempt": attempt,
        }

    return hold_placement
