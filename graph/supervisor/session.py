"""Session entry: version pinning, turn baselines, and fresh-session state."""

from __future__ import annotations

import uuid
from typing import Any

from graph import nodes as N
from graph.bookings import turn_outcomes
from graph.models import (
    RequestKind,
    TurnBaseline,
)
from graph.routing.kinds import PreviousTurn
from graph.state import ResortFlowState
from graph.supervisor.common import GRAPH_VERSION

# ============================================================================
# Node factories
# ============================================================================


def make_entry_node() -> Any:
    """Pre-`IntentRouter` version pinning and no-silent-resume gate
    (T021, spec.md User Story 5 Acceptance Scenario 2). Not itself a name in
    `contracts/graph-topology-v005.md`'s diagram — it is where `START`'s
    single edge lands, immediately ahead of the `IntentRouter`/`HumanHandoff`
    fork, so it never needs its own reserved node name."""

    async def entry(state: ResortFlowState) -> dict[str, Any]:
        update: dict[str, Any] = {}
        if not state.get("graph_version"):
            update["graph_version"] = GRAPH_VERSION
        return update

    return entry


def _route_from_entry(state: ResortFlowState) -> str:
    stored_version = state.get("graph_version") or "0"
    current_major = GRAPH_VERSION.split(".", 1)[0]
    stored_major = stored_version.split(".", 1)[0]
    if stored_major != current_major and state.get("holds"):
        return N.RELEASE  # version mismatch with live holds: release, then respond asking restart
    if state.get("escalate"):
        # Constitution Principle V / spec.md US5 Acceptance Scenario 2: a
        # follow-up message on an already-escalated thread must not
        # silently resume automated processing.
        return N.HUMAN_HANDOFF
    # Feature 006: distress screening (POLICY_PREFLIGHT) must run ahead of
    # IntentRouter's fork, not only on the SUPERVISOR (multi-domain) branch —
    # otherwise the ~60% of traffic routed to DETERMINISTIC (Constitution
    # Principle I) would never be screened at all. (This moved from feature 005's
    # original SUPERVISOR-only wiring.)
    return N.POLICY_PREFLIGHT


def _compute_turn_baseline(state: ResortFlowState) -> TurnBaseline:
    """Feature 010 (ADR-056): a snapshot of the booking-record keys/
    statuses as they stand right now — before this turn's own actions run,
    since `INTENT_ROUTER` is always the first node in a turn. `turn_index`
    increments from the previous baseline (1 on the very first routed
    turn of a conversation, or on a pre-010 thread's first post-upgrade
    turn — indistinguishable from a fresh conversation, which is fine:
    `turn_index` is not used for reporting in this feature)."""
    confirmations = state.get("confirmations") or []
    holds = state.get("holds") or []
    previous_baseline = state.get("turn_baseline")
    return TurnBaseline(
        confirmation_keys=sorted({c.idempotency_key for c in confirmations}),
        hold_statuses={h.idempotency_key: h.status for h in holds},
        turn_index=(previous_baseline.turn_index + 1) if previous_baseline is not None else 1,
    )


def _compute_previous_turn(state: ResortFlowState) -> PreviousTurn | None:
    """Feature 010 (research R-09, FR-008a): derives this turn's carry-over
    eligibility from the PRIOR `routing` decision — read here, before this
    call overwrites it. Eligible only for a prior `book` that produced a
    new booking record (`turn_outcomes`, computed against the state as it
    stands now — i.e. before this turn's own actions — which is exactly
    what the previous turn produced) or a prior `lookup` that matched at
    least one record (`focus_keys`)."""
    prior = state.get("routing")
    if prior is not None and prior.kind == RequestKind.BROWSE:
        # Feature 011 (FR-005a): the immediately previous browse — services, filters, window — so a short
        # follow-up can narrow it. Read from the previous decision, never from message text; one turn only
        # because `routing` is overwritten every turn.
        return PreviousTurn(
            kind=RequestKind.BROWSE, services=list(prior.services), had_booking=False,
            filters=prior.browse_filters, window=prior.window,
        )
    if prior is None or prior.kind not in (RequestKind.BOOK, RequestKind.LOOKUP):
        return None
    if prior.kind == RequestKind.BOOK:
        outcomes = turn_outcomes(state) or []
        if not outcomes:
            return None
        return PreviousTurn(
            kind=prior.kind,
            services=[o.service for o in outcomes],
            had_booking=True,
            focus_keys=[o.key for o in outcomes],
        )
    # RequestKind.LOOKUP
    if not prior.focus_keys:
        return None
    return PreviousTurn(
        kind=prior.kind, services=prior.services, had_booking=True, focus_keys=prior.focus_keys
    )


def new_session_state(*, tenant_id: str, guest_id: str, session_id: str | None = None, text: str) -> ResortFlowState:
    from langchain_core.messages import HumanMessage

    return ResortFlowState(
        tenant_id=tenant_id,
        guest_id=guest_id,
        session_id=session_id or uuid.uuid4().hex,
        jurisdiction="NV" if tenant_id == "MGV" else "MA",
        entitlements=[],
        saga_id=uuid.uuid4().hex,
        messages=[HumanMessage(content=text)],
        plan=None,
        replan_count=0,
        proposals=[],
        holds=[],
        confirmations=[],
        failures=[],
        retrieved=[],
        citations=[],
        awaiting_approval=False,
        approval_scope=None,
        policy_flags=[],
        escalate=False,
        saga_log=[],
        graph_version=GRAPH_VERSION,
        clarification_count=0,
        escalate_reason=None,
        routing=None,
        turn_baseline=None,
    )
