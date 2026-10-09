"""T050 (stay) — Hold placement: MCP `hold_room`, constructs
`graph.models.Hold` (status=`HoldStatus.HELD`, `expires_at` required) and
merges it into `state["holds"]` via `merge_holds`. Mirrors
`agents.dining.nodes.hold_placement` exactly, including the write-ahead
ledger sequencing (`graph.ledger.HoldLedger`, ADR-019): `insert_pending` is
called *before* the vendor call, so a crash between here and the vendor
responding leaves a `PENDING` row a reaper can reconcile.

Stay-specific wrinkle: since `room_discovery` has no separate availability
step (see that module's docstring), the selected `Proposal` never gets a
`starts_at`/`ends_at` stamped onto it upstream the way dining's `availability`
node does. This node computes the arrival/departure window from the parsed
intent and stamps it onto the proposal here — `starts_at` at arrival
midnight, `ends_at` at departure midnight — purely so
`graph.supervisor`'s deterministic cross-domain conflict checker has
*something* to compare a multi-night stay against. A same-evening
schedule-overlap check against a multi-day span is a degenerate case (every
same-day dining/wellness/entertainment proposal will trivially "overlap" a
stay), but that is a pre-existing conflict-checker granularity limitation,
not something this node should silently work around by leaving the fields
`None` (an empty window is a worse contract violation than a wide one).

Every stay booking requires human sign-off before commit, unconditionally —
same pattern as `agents.wellness.nodes.hold_placement` (no dedicated
conflict-detection node exists for stay the way `allergen_check` does for
dining), so `hold_placement` sets `awaiting_approval = True` on every
successful hold.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from agents.stay.nodes.room_discovery import offer_id_from_proposal_id
from agents.stay.state import StayState
from agents.stay.tools import StayToolError, StayTools, idempotency_key
from graph.errors import classify
from graph.ledger import HoldLedger
from graph.models import Domain, Hold, HoldStatus, Proposal, ToolFailure


def make_hold_placement_node(tools: StayTools, ledger: HoldLedger) -> Any:
    async def hold_placement(state: StayState) -> dict[str, Any]:
        selected_id = state.get("selected_proposal_id")
        proposal: Proposal | None = None
        for candidate in state.get("proposals", []):
            if candidate.proposal_id == selected_id:
                proposal = candidate
                break
        if proposal is None:
            return {}

        intent = state.get("intent") or {}
        date_str = intent.get("date_str")
        if date_str is None:
            # No resolvable arrival date — nothing to hold. Found live (demo
            # report): this used to fire on *every* multi-domain request,
            # silently and unconditionally, because `room_discovery` only
            # ever applied its own "default to tomorrow" locally, never
            # persisting it back into `state["intent"]` — this bare `return
            # {}` is genuinely reachable now only when no date could be
            # resolved (or defaulted) at all. Still a silent no-op rather
            # than a guest-facing message; a real "we couldn't tell when"
            # disclosure is a follow-up UX decision, not fixed here.
            return {}
        nights = intent.get("nights") or 1
        arrival_date = date.fromisoformat(date_str)
        departure_date = arrival_date + timedelta(days=nights)
        proposal = proposal.model_copy(
            update={
                "starts_at": datetime.combine(arrival_date, time.min),
                "ends_at": datetime.combine(departure_date, time.min),
            }
        )

        attempt = state.get("hold_attempt", 0) + 1
        key = idempotency_key(state["session_id"], proposal.proposal_id, attempt)
        offer_id = offer_id_from_proposal_id(proposal.proposal_id)

        await ledger.insert_pending(
            saga_id=state["saga_id"],
            session_id=state["session_id"],
            tenant_id=state["tenant_id"],
            domain=Domain.STAY,
            idempotency_key=key,
        )

        try:
            vendor_hold = await tools.hold_room(
                offer_id=offer_id,
                arrival=date_str,
                departure=departure_date.isoformat(),
                idempotency_key=key,
            )
        except StayToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.STAY,
                tool="hold_room",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            hold = Hold(
                idempotency_key=key,
                domain=Domain.STAY,
                tenant_id=state["tenant_id"],
                saga_id=state["saga_id"],
                status=HoldStatus.RELEASED,
                proposal_id=proposal.proposal_id,
            )
            await ledger.mark_released(idempotency_key=key)
            return {
                "failures": [failure],
                "holds": [hold],
                "proposals": [proposal],
                "hold_attempt": attempt,
            }

        expires_at = datetime.fromisoformat(vendor_hold.EXPIRES_AT)
        await ledger.mark_held(idempotency_key=key, hold_id=vendor_hold.HOLD_ID, expires_at=expires_at)
        hold = Hold(
            idempotency_key=key,
            hold_id=vendor_hold.HOLD_ID,
            domain=Domain.STAY,
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
            "awaiting_approval": True,
            "approval_scope": f"stay_booking:{proposal.proposal_id}",
        }

    return hold_placement
