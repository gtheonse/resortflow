"""T027 — Confirm/purchase: MCP `get_order_status` (revalidate) then
`purchase_tickets` (commit), promotes a `Hold` to a `graph.models.Confirmation`
appended to `state["confirmations"]`. Mounted under the `PURCHASE` node name
(`agents.entertainment.edges.conditions.PURCHASE`) — this module is named
`confirm.py` to mirror `agents/dining/nodes/confirm.py`'s file layout, since
the pattern it implements (revalidate-then-commit) is identical.

AGENTS.md hard rule: never trust `state["holds"]` on resume — revalidate
against the vendor. This node revalidates unconditionally (not only when
resumed — there is no reliable in-node signal distinguishing "just placed
this superstep" from "resumed after a checkpoint", and revalidating always
is strictly safer) via `get_order_status(hold_id=...)` before ever calling
`purchase_tickets`. A hold no longer `"active"` classifies to
`REQUIRES_HUMAN` (`graph.errors.classify`) and escalates rather than
silently retrying: a fresh hold requires going back through
`hold_placement`, which is a human/orchestrator decision.

Simplification (disclosed): `purchase_tickets` is async — the real vendor
confirms the order asynchronously via webhook (`PurchaseResponse.status`
starts `"PENDING"`) — so this node constructs the `Confirmation` optimistically
from the `PurchaseResponse.order_id` the moment the purchase call is accepted,
rather than polling for the webhook-driven terminal status; full
webhook-driven confirmation polling is out of scope for this feature.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agents.entertainment.state import EntertainmentState
from agents.entertainment.tools import EntertainmentToolError, EntertainmentTools
from graph.errors import classify
from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, HoldStatus, Proposal, ToolFailure

_OPTIMISTIC_CANCELLATION_WINDOW = timedelta(hours=24)


def _find_proposal(state: EntertainmentState) -> Proposal | None:
    selected_id = state.get("selected_proposal_id")
    for candidate in state.get("proposals", []):
        if candidate.proposal_id == selected_id:
            return candidate
    return None


def _find_hold(state: EntertainmentState, idempotency_key: str) -> Hold | None:
    for hold in state.get("holds", []):
        if hold.idempotency_key == idempotency_key:
            return hold
    return None


def make_confirm_node(tools: EntertainmentTools, ledger: HoldLedger) -> Any:
    async def confirm(state: EntertainmentState) -> dict[str, Any]:
        proposal = _find_proposal(state)
        if proposal is None or proposal.hold_idempotency_key is None:
            return {}
        hold = _find_hold(state, proposal.hold_idempotency_key)
        if hold is None or hold.hold_id is None or hold.status != HoldStatus.HELD:
            return {}

        # --- Revalidate against the vendor before ever purchasing -----------
        try:
            status = await tools.get_order_status(hold_id=hold.hold_id)
        except EntertainmentToolError as exc:
            return await _expire(ledger, state, hold, exc.vendor_code, exc.http_status, exc.detail)

        if status.hold is None or status.hold.status != "active":
            return await _expire(
                ledger, state, hold, "HOLD_EXPIRED", 410, "hold no longer active at vendor"
            )

        # --- Purchase --------------------------------------------------------
        try:
            purchase = await tools.purchase_tickets(
                hold_id=hold.hold_id,
                guest={"guest_id": state["guest_id"]},
                idempotency_key=hold.idempotency_key,
            )
        except EntertainmentToolError as exc:
            return await _expire(ledger, state, hold, exc.vendor_code, exc.http_status, exc.detail)

        await ledger.mark_confirmed(idempotency_key=hold.idempotency_key)
        confirmed_hold = hold.model_copy(update={"status": HoldStatus.CONFIRMED})
        now = datetime.now(UTC)
        confirmation = Confirmation(
            confirmation_id=purchase.order_id,
            idempotency_key=hold.idempotency_key,
            domain=Domain.ENTERTAINMENT,
            confirmed_at=now,
            cancellation_deadline=now + _OPTIMISTIC_CANCELLATION_WINDOW,
        )
        return {"holds": [confirmed_hold], "confirmations": [confirmation]}

    return confirm


async def _expire(
    ledger: HoldLedger, state: EntertainmentState, hold: Hold,
    vendor_code: str | None, http_status: int | None, detail: str,
) -> dict[str, Any]:
    error_class = classify(vendor_code, http_status, None)
    failure = ToolFailure(
        domain=Domain.ENTERTAINMENT,
        tool="purchase_tickets",
        error_class=error_class,
        vendor_code=vendor_code if isinstance(vendor_code, str) else None,
        http_status=http_status,
        detail=detail,
    )
    await ledger.mark_expired(idempotency_key=hold.idempotency_key)
    expired_hold = hold.model_copy(update={"status": HoldStatus.EXPIRED})
    return {
        "holds": [expired_hold],
        "failures": [failure],
        "awaiting_approval": True,
        "approval_scope": f"hold_expired:{hold.proposal_id}",
    }
