"""T048 — Availability check: MCP `check_availability`, refines the
selected `Proposal`'s `starts_at`/`ends_at`. No availability -> waitlist
(MCP `join_waitlist`) rather than a dead end.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from langchain_core.messages import AIMessage

from agents.dining.nodes.venue_discovery import venue_id_from_proposal_id
from agents.dining.state import DiningState
from agents.dining.tools import DiningToolError, DiningTools
from graph.errors import classify
from graph.models import Domain, Proposal, ToolFailure

RESERVATION_DURATION_MINUTES = 90
DEFAULT_EARLIEST = "18:00"


def _select_slot(slots: list, preferred_time: str | None):
    if not slots:
        return None
    if preferred_time is None:
        return slots[0]
    return min(slots, key=lambda s: abs(_minutes(s.time) - _minutes(preferred_time)))


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def make_availability_node(tools: DiningTools, *, today: date | None = None):
    def _find_proposal(state: DiningState) -> Proposal | None:
        selected_id = state.get("selected_proposal_id")
        if not selected_id:
            return None
        for proposal in state.get("proposals", []):
            if proposal.proposal_id == selected_id:
                return proposal
        return None

    async def availability(state: DiningState) -> dict:
        proposal = _find_proposal(state)
        if proposal is None:
            return {"waitlisted": False}

        intent = state.get("intent") or {}
        resolved_today = today or date.today()  # noqa: DTZ011 — local wall-clock date is correct here
        date_str = intent.get("date_str") or (resolved_today + timedelta(days=1)).isoformat()
        venue_id = venue_id_from_proposal_id(proposal.proposal_id)
        party_size = proposal.party_size or 2

        try:
            response = await tools.check_availability(
                venue_id=venue_id,
                date=date_str,
                party_size=party_size,
                earliest=intent.get("time_str") or DEFAULT_EARLIEST,
            )
        except DiningToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.DINING,
                tool="check_availability",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            return {"failures": [failure], **await _waitlist(tools, venue_id, party_size)}

        slot = _select_slot(response.slots, intent.get("time_str"))
        if slot is None:
            return await _waitlist(tools, venue_id, party_size)

        starts_at = datetime.fromisoformat(f"{date_str}T{slot.time}:00")
        updated = proposal.model_copy(
            update={
                "starts_at": starts_at,
                "ends_at": starts_at + timedelta(minutes=RESERVATION_DURATION_MINUTES),
            }
        )
        return {"proposals": [updated], "waitlisted": False}

    async def _waitlist(tools: DiningTools, venue_id: str, party_size: int) -> dict:
        key = f"waitlist:{venue_id}:{party_size}"
        try:
            result = await tools.join_waitlist(
                venue_id=venue_id, party_size=party_size, idempotency_key=key
            )
        except DiningToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.DINING,
                tool="join_waitlist",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            return {
                "failures": [failure],
                "waitlisted": False,
                "messages": [
                    AIMessage(content="No availability, and the waitlist could not be joined.")
                ],
            }
        return {
            "waitlisted": True,
            "waitlist_id": result.waitlist_id,
            "messages": [
                AIMessage(
                    content=(
                        f"No table available at that time — added to the waitlist "
                        f"(position {result.position}, ~{result.quoted_wait_minutes} min quoted)."
                    )
                )
            ],
        }

    return availability
