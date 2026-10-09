"""T052 (stay) — Confirm: MCP `confirm_room_reservation`, promotes a `Hold`
to a `graph.models.Confirmation` appended to `state["confirmations"]`.

AGENTS.md hard rule: never trust `state["holds"]` on resume — revalidate
against the vendor. Stay has no separate status-check tool the way dining
has `check_dining_status` (`mcp/gateway/stay/server.py` publishes no such
tool), so — mirroring `agents.wellness.nodes.confirm`'s identical choice —
this node revalidates by re-calling `search_rooms` for the same
arrival/departure/room_class and confirming the held `offer_id` is still
listed among the results with `AVAILABLE_UNITS > 0`, the closest stay
equivalent to "hold still active" available from the frozen tool surface.
Revalidation runs unconditionally (not only when resumed — there is no
reliable in-node signal distinguishing "just placed this superstep" from
"resumed after a checkpoint", and revalidating always is strictly safer).
An offer no longer listed (or listed with no remaining units) classifies as
`HOLD_EXPIRED` -> `REQUIRES_HUMAN` (`graph.errors.classify`) and escalates
rather than silently retrying: a fresh hold requires going back through
`hold_placement`, a human/orchestrator decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agents.stay.nodes.room_discovery import offer_id_from_proposal_id
from agents.stay.state import StayState
from agents.stay.tools import StayToolError, StayTools
from graph.errors import classify
from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, HoldStatus, Proposal, ToolFailure

PLACEHOLDER_PAYMENT_TOKEN = "resortflow-internal-guest-payment-token"
"""No real payment integration exists in this vertical slice — mirrors
`agents.entertainment.nodes.confirm`'s identical placeholder pattern for
`purchase_tickets`'s `payment_token`."""


def _find_proposal(state: StayState) -> Proposal | None:
    selected_id = state.get("selected_proposal_id")
    for candidate in state.get("proposals", []):
        if candidate.proposal_id == selected_id:
            return candidate
    return None


def _find_hold(state: StayState, idempotency_key: str) -> Hold | None:
    for hold in state.get("holds", []):
        if hold.idempotency_key == idempotency_key:
            return hold
    return None


def make_confirm_node(tools: StayTools, ledger: HoldLedger) -> Any:
    async def confirm(state: StayState) -> dict[str, Any]:
        proposal = _find_proposal(state)
        if proposal is None or proposal.hold_idempotency_key is None:
            return {}
        hold = _find_hold(state, proposal.hold_idempotency_key)
        if hold is None or hold.hold_id is None or hold.status != HoldStatus.HELD:
            return {}
        if proposal.starts_at is None or proposal.ends_at is None:
            # No arrival/departure window to revalidate against at all.
            return await _expire(
                ledger, state, hold, "HOLD_EXPIRED", 410, "proposal lost its stay window before confirm"
            )

        # --- Revalidate against the vendor before ever confirming ----------
        # No dedicated status-check tool exists for stay — re-run the same
        # search that produced the offer and confirm it is still listed with
        # inventory remaining (see module docstring).
        offer_id = offer_id_from_proposal_id(proposal.proposal_id)
        arrival = proposal.starts_at.date().isoformat()
        departure = proposal.ends_at.date().isoformat()
        try:
            offers = await tools.search_rooms(
                arrival=arrival, departure=departure, adults=proposal.party_size or 2
            )
        except StayToolError as exc:
            return await _expire(ledger, state, hold, exc.vendor_code, exc.http_status, exc.detail)

        still_available = any(
            offer.OFFER_ID == offer_id and offer.AVAILABLE_UNITS > 0 for offer in offers
        )
        if not still_available:
            return await _expire(
                ledger, state, hold, "HOLD_EXPIRED", 410, "hold no longer active at vendor"
            )

        # --- Confirm -----------------------------------------------------
        try:
            reservation = await tools.confirm_room_reservation(
                hold_id=hold.hold_id,
                guest_profile_id=state["guest_id"],
                payment_token=PLACEHOLDER_PAYMENT_TOKEN,
                idempotency_key=hold.idempotency_key,
            )
        except StayToolError as exc:
            return await _expire(ledger, state, hold, exc.vendor_code, exc.http_status, exc.detail)

        await ledger.mark_confirmed(idempotency_key=hold.idempotency_key)
        confirmed_hold = hold.model_copy(update={"status": HoldStatus.CONFIRMED})
        confirmation = Confirmation(
            confirmation_id=reservation.CONFIRMATION_NUMBER,
            idempotency_key=hold.idempotency_key,
            domain=Domain.STAY,
            confirmed_at=datetime.now(UTC),
            cancellation_deadline=None,
            # No cancellation-deadline field is published by
            # `RoomReservation` (mcp/gateway/stay/models.py) — left `None`
            # rather than fabricated.
        )
        return {"holds": [confirmed_hold], "confirmations": [confirmation]}

    return confirm


async def _expire(
    ledger: HoldLedger, state: StayState, hold: Hold,
    vendor_code: str | None, http_status: int | None, detail: str,
) -> dict[str, Any]:
    error_class = classify(vendor_code, http_status, None)
    failure = ToolFailure(
        domain=Domain.STAY,
        tool="confirm_room_reservation",
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
