"""T050 — Hold placement: MCP `hold_table`, constructs `graph.models.Hold`
(status=`HoldStatus.HELD`, `expires_at` required) and merges it into
`state["holds"]` via `merge_holds`. Keyed by `idempotency_key` — `hold_id`
is vendor-assigned and stays `None` in `graph.models.Hold` terms only if
the vendor call fails; on success the vendor's `hold_id` is recorded too,
purely as a display/debug field, never as the merge key.

Write-ahead ledger (`graph.ledger.HoldLedger`, ADR-019): `insert_pending`
is called *before* the vendor call, so a crash between here and the vendor
responding leaves a `PENDING` row a reaper can reconcile — this is
precisely the first of the three crash windows `infra/sql/001_hold_ledger.sql`
documents. `mark_held`/`mark_released` record the outcome once known. The
ledger is a side channel to `state["holds"]`, not a replacement for it —
`ResortFlowState` still owns the conversation-visible `Hold`; the ledger owns
proof of the external side effect independent of the checkpointer
(Constitution Principle V: these two records must never be conflated).
"""

from __future__ import annotations

from datetime import datetime

from agents.dining.nodes.venue_discovery import venue_id_from_proposal_id
from agents.dining.state import DiningState
from agents.dining.tools import DiningToolError, DiningTools, idempotency_key
from graph.errors import classify
from graph.ledger import HoldLedger
from graph.models import Domain, Hold, HoldStatus, Proposal, ToolFailure


def make_hold_placement_node(tools: DiningTools, ledger: HoldLedger):
    async def hold_placement(state: DiningState) -> dict:
        selected_id = state.get("selected_proposal_id")
        proposal: Proposal | None = None
        for candidate in state.get("proposals", []):
            if candidate.proposal_id == selected_id:
                proposal = candidate
                break
        if proposal is None or proposal.starts_at is None:
            # No confirmed slot (e.g. waitlisted) — nothing to hold.
            return {}

        attempt = state.get("hold_attempt", 0) + 1
        key = idempotency_key(state["session_id"], proposal.proposal_id, attempt)
        venue_id = venue_id_from_proposal_id(proposal.proposal_id)

        await ledger.insert_pending(
            saga_id=state["saga_id"],
            session_id=state["session_id"],
            tenant_id=state["tenant_id"],
            domain=Domain.DINING,
            idempotency_key=key,
        )

        try:
            vendor_hold = await tools.hold_table(
                venue_id=venue_id,
                date=proposal.starts_at.date().isoformat(),
                time=proposal.starts_at.strftime("%H:%M"),
                party_size=proposal.party_size or 2,
                idempotency_key=key,
            )
        except DiningToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.DINING,
                tool="hold_table",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            hold = Hold(
                idempotency_key=key,
                domain=Domain.DINING,
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
            domain=Domain.DINING,
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
