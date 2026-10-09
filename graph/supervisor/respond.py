"""Guest-facing reply composition for the `RESPOND` node."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from graph.bookings import booking_view, turn_outcomes
from graph.replies import (
    format_current_status_line,
    format_turn_outcome_line,
)
from graph.state import ResortFlowState


async def _compose_respond_message(state: dict[str, Any]) -> dict[str, Any]:
    """Core logic behind `RESPOND` (T069, FR-014, FR-021, spec.md User Story 2
    Acceptance Scenario 3), extracted to a plain function — mirroring
    `_apply_policy_postflight`/`_apply_offer_agent` above — so
    `make_deterministic_node` can call it directly. `DETERMINISTIC` routes
    straight to `END`, never through the `RESPOND` node (see that node's
    module-level routing), which left every single-domain confirm (~60% of
    traffic, Constitution Principle I) with no guest-facing confirmation
    message at all: `confirm.py`'s success path never appends one, and
    nothing downstream did either — found via the feature 008 verification
    console, not previously covered by an existing test. Calling this same
    function directly from both places, guarded by the caller checking
    whether the inner subgraph already produced its own message (allergen
    block, "couldn't tell what you'd like to book", etc.), avoids
    double-messaging exactly the way `_apply_policy_postflight`'s "RELEASE
    already composed its own message" check does."""
    if state.get("escalate"):
        return {
            "messages": [
                AIMessage(
                    content=f"I've connected this request with a team member ({state.get('escalate_reason', 'escalated')})."
                )
            ]
        }

    # Feature 010 (FR-031, research R-12): built from THIS TURN's outcomes
    # only, never the cumulative `confirmations` channel — that cumulative
    # read is exactly what produced a stale "confirmed" reply on a later,
    # uneventful turn (the live incident behind this feature).
    outcomes = turn_outcomes(state)
    if outcomes is None:
        # No `turn_baseline` — a pre-010 conversation's first post-upgrade
        # turn. "Every record is new" would report an old confirmation as
        # confirmed by this turn, which FR-031 forbids; fall back to the
        # lookup-style "here's where things stand" presentation instead
        # (contracts/turn-replies.md).
        records = booking_view(state)
        if not records:
            return {"messages": [AIMessage(content="Nothing was booked for this request.")]}
        lines = "\n".join(format_current_status_line(r) for r in records)
        return {"messages": [AIMessage(content=f"Here's where things stand:\n{lines}")]}

    failures = state.get("failures", [])
    recent_kinds = {e.kind for e in (state.get("saga_log") or [])[-10:]}
    if not outcomes:
        if "released" in recent_kinds:
            # RELEASE already composed its own guest-facing message
            # (FR-022) — do not stomp on it with a generic one.
            return {}
        return {"messages": [AIMessage(content="Nothing was booked for this request.")]}

    confirmed_this_turn = [o for o in outcomes if o.status == "confirmed"]
    lines = "\n".join(format_turn_outcome_line(o) for o in outcomes)
    if failures or "compensated" in recent_kinds:
        # `lines` (from `turn_outcomes`, this turn's real status per item)
        # is already the full detail — no need to point at a separate
        # recovery message for it (found live, demo report: that message
        # used to be the only place the alternative's *name* appeared, but
        # could itself be stale/false if the alternative's own commit later
        # failed too; `lines` is always the true, current status).
        content = f"{lines}\nOne part needed an alternative."
    elif "partial_itinerary_proposed" in recent_kinds:
        content = f"{lines}\nOne or more requested items had no availability."
    elif len(confirmed_this_turn) == len(outcomes):
        content = f"All set — {lines}" if len(outcomes) == 1 else f"All set:\n{lines}"
    else:
        content = lines
    return {"messages": [AIMessage(content=content)]}


def make_respond_node() -> Any:
    """Thin node wrapper around `_compose_respond_message` — see that
    function's docstring for the real logic and why it's a standalone
    function, not inlined here."""

    async def respond(state: ResortFlowState) -> dict[str, Any]:
        return await _compose_respond_message(state)

    return respond
