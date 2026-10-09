"""Multi-domain planning: planner, fan-out, conflict check, proposal assembly and the cross-domain approval gate."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from graph import nodes as N
from graph.ledger import HoldLedger
from graph.models import (
    Conflict,
    Domain,
    HoldStatus,
    Plan,
    PlanTask,
    Proposal,
    RequestKind,
    SagaEvent,
)
from graph.state import MAX_REPLAN_COUNT, ResortFlowState
from graph.supervisor.common import (
    _REGISTRY,
    _detect_domains,
    _find_hold_for_proposal,
    _latest_human_text,
    _next_saga_sequence,
)
from graph.travel_time import UnknownZoneError, travel_minutes


def make_supervisor_node() -> Any:
    """Multi-domain entry — a pass-through straight to `PLANNER`.
    `POLICY_PREFLIGHT` (distress screening) already ran once, immediately
    after `ENTRY`, ahead of `IntentRouter`'s fork — it does not sit between
    `SUPERVISOR` and `PLANNER` the way feature 005 originally reserved it,
    since that placement would have left the `DETERMINISTIC` branch (~60% of
    traffic) unscreened."""

    async def supervisor(state: ResortFlowState) -> dict[str, Any]:
        return {}

    return supervisor


def make_planner_node() -> Any:
    """Deterministic domain decomposition (see module docstring) + the
    bounded replan cycle (`MAX_REPLAN_COUNT`, T018)."""

    async def planner(state: ResortFlowState) -> dict[str, Any]:
        replan_count = state.get("replan_count", 0)
        if state.get("plan") is not None:
            # A return trip through PLANNER (post-ConflictCheck replan, or
            # HumanApproval: modified) — bump the bound before doing
            # anything else.
            replan_count += 1
            if replan_count > MAX_REPLAN_COUNT:
                return {
                    "replan_count": replan_count,
                    "escalate": True,
                    "escalate_reason": "replan_bound",
                    "messages": [
                        AIMessage(
                            content="I wasn't able to find a workable combination after several attempts — connecting you with a team member."
                        )
                    ],
                }

        text = _latest_human_text(state)
        # Feature 010: read INTENT_ROUTER's decision — set once for this
        # turn and stable across a replan cycle (no new human message
        # arrives between conflict-check failures within one turn), so
        # re-reading it on each PLANNER pass is equivalent to the old
        # per-pass re-scan, without a second parse of the message.
        routing = state.get("routing")
        if routing is not None and routing.kind in (RequestKind.LOOKUP, RequestKind.BROWSE) and routing.secondary_services:
            domains = list(routing.secondary_services)
        elif routing is not None and routing.services:
            domains = list(routing.services)
        else:
            domains = _detect_domains(text)
        # Domains with zero proposals from a prior FanOut pass are silently
        # dropped from the next plan and logged (spec.md's clarified
        # FR-021: a partial itinerary, not a hard failure).
        existing_proposal_domains = {p.domain for p in state.get("proposals", [])}
        saga_log_updates: list[SagaEvent] = []
        if replan_count > 0 and existing_proposal_domains:
            dropped = [d for d in domains if d not in existing_proposal_domains]
            domains = [d for d in domains if d in existing_proposal_domains] or domains
            for d in dropped:
                saga_log_updates.append(
                    SagaEvent(
                        event_id=uuid.uuid4().hex,
                        saga_id=state["saga_id"],
                        sequence=_next_saga_sequence(state) + len(saga_log_updates),
                        kind="partial_itinerary_proposed",
                        domain=d,
                        occurred_at=datetime.now(UTC),
                        detail={"reason": "no_availability"},
                    )
                )

        tasks = [PlanTask(task_id=f"task-{d.value}", domain=d) for d in domains]
        plan = Plan(plan_id=state.get("saga_id", uuid.uuid4().hex), tasks=tasks, created_at=datetime.now(UTC))
        update: dict[str, Any] = {"plan": plan, "replan_count": replan_count}
        if saga_log_updates:
            update["saga_log"] = saga_log_updates
        return update

    return planner


_FANOUT_DOMAINS: Final[tuple[Domain, ...]] = _REGISTRY.fanout_domains()
"""Saga participants only, in registration order. Gaming is read-only: it implements
`StandaloneDomain` but not `SagaParticipant`, so it never appears here."""

_FANOUT_DOMAIN_NODES: Final[tuple[str, ...]] = tuple(d.value for d in _FANOUT_DOMAINS)


def _route_from_planner(state: ResortFlowState) -> str | list[str]:
    """A list return fans out to every listed node in the same superstep —
    LangGraph's documented mechanism for dynamic parallel fan-out from one
    conditional-edge function, used here instead of separate unconditional
    edges (which cannot coexist with a conditional-edge function on the same
    source node)."""
    if state.get("escalate"):
        return N.HUMAN_HANDOFF
    return list(_FANOUT_DOMAIN_NODES)


_OWN_APPROVAL_KEYS: Final[tuple[str, ...]] = ("awaiting_approval", "approval_scope")
"""Bug found while building T073 (`/speckit-converge`'s T072 follow-on):
wellness's and stay's `hold_placement` nodes (their own standalone-graph
shape, correctly) set `awaiting_approval=True` unconditionally — but when
reused as a FanOut runner's *prefix* (module docstring: FanOut never invokes
a domain's own approval/confirm), that key leaked into the branch's return
dict. `awaiting_approval` is a plain, non-`Annotated` `ResortFlowState` key
(`LastValue` channel) — two FanOut branches both writing it in the same
superstep (e.g. a plan containing both wellness and stay) raised LangGraph's
`InvalidUpdateError`, crashing any itinerary combining those two domains.
`ProposalAssembly` is FanOut's own, single, cross-domain owner of this
decision (`make_proposal_assembly_node`) — no individual domain branch may
contribute to it, so both keys are stripped here, once, at FanOut's own
boundary, rather than patched into every runner that happens to reuse a
node with this shape today or added in the future."""


def make_fanout_domain_node(domain: Domain, tools_factory: dict[Domain, Callable[..., Any]], ledger: HoldLedger) -> Any:
    """One FanOut branch. A no-op (`{}`) when `domain` is not part of the
    current `Plan` — this is what lets every domain node share a single,
    unconditional edge set from `PLANNER` (`docs/ResortFlow-HITL-Durability-State.md`
    §2's fan-out-superstep guarantee: independent branches, no cross-branch
    ordering, safe to resume individually)."""
    participant = _REGISTRY.participant(domain)

    async def fanout_domain(state: ResortFlowState) -> dict[str, Any]:
        plan = state.get("plan")
        if plan is None or not any(t.domain == domain for t in plan.tasks):
            return {}
        factory = tools_factory.get(domain)
        if participant is None or factory is None:  # pragma: no cover — domain not yet wired, or no tools configured
            return {}
        tools = factory(state["tenant_id"], state["guest_id"])
        result: dict[str, Any] = await participant.propose_and_hold(state, tools, ledger)
        for key in _OWN_APPROVAL_KEYS:
            result.pop(key, None)
        return result

    return fanout_domain


def _as_aware(dt: datetime) -> datetime:
    """Normalize to timezone-aware UTC. Different domains' availability
    nodes produce naive (dining: `datetime.fromisoformat(f"{date}T{time}:00")`)
    and aware (entertainment: parsed from the vendor's `start_utc`) values —
    the conflict checker is the one place that must compare across domains,
    so it is the one place that must reconcile this rather than assuming a
    single domain's convention. Naive values are treated as already UTC,
    matching every domain's own wall-clock convention."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def make_conflict_check_node() -> Any:
    """T017 — deterministic, no LLM (Constitution: DETERMINISM FIRST). Pairwise
    over every proposal with both a schedule and a hold in place.

    `Domain.STAY` proposals are excluded from this pairwise comparison
    entirely. A stay's `starts_at`/`ends_at` spans full days (arrival
    midnight to departure midnight — `agents/stay/nodes/hold_placement.py`'s
    own documented choice, made purely so *some* window exists), so a naive
    schedule-overlap check would flag every same-evening dining/
    entertainment/wellness proposal as conflicting with it, always — a stay
    is the itinerary's backdrop, not a same-evening scheduling commitment
    the way a dinner reservation or a show is. Excluding it here is what
    actually makes a "dinner, a show, and a room" itinerary possible at all,
    not a workaround for a hypothetical case."""

    async def conflict_check(state: ResortFlowState) -> dict[str, Any]:
        held_proposals: list[tuple[Proposal, datetime, datetime]] = []
        for p in state.get("proposals", []):
            if p.starts_at is None or p.ends_at is None or p.domain is Domain.STAY:
                continue
            h = _find_hold_for_proposal(state, p)
            if h is None or h.status != HoldStatus.HELD:
                continue
            held_proposals.append((p, _as_aware(p.starts_at), _as_aware(p.ends_at)))

        conflicts: list[Conflict] = []
        for i, (a, a_start, a_end) in enumerate(held_proposals):
            for b, b_start, b_end in held_proposals[i + 1 :]:
                if a_start < b_end and b_start < a_end:
                    conflicts.append(
                        Conflict(
                            conflict_id=uuid.uuid4().hex,
                            kind="schedule_overlap",
                            proposal_ids=[a.proposal_id, b.proposal_id],
                            detail=f"{a.title} ({a_start}-{a_end}) overlaps {b.title} ({b_start}-{b_end})",
                        )
                    )
                    continue
                if a.location_zone and b.location_zone and a.tenant_id == b.tenant_id:
                    first, first_end, second, second_start = (
                        (a, a_end, b, b_start) if a_start <= b_start else (b, b_end, a, a_start)
                    )
                    assert first.location_zone is not None and second.location_zone is not None  # narrowed above
                    gap_minutes = (second_start - first_end).total_seconds() / 60
                    if gap_minutes >= 0:
                        try:
                            needed = travel_minutes(a.tenant_id, first.location_zone, second.location_zone)
                        except UnknownZoneError:
                            needed = 0
                        if gap_minutes < needed:
                            conflicts.append(
                                Conflict(
                                    conflict_id=uuid.uuid4().hex,
                                    kind="travel_infeasible",
                                    proposal_ids=[a.proposal_id, b.proposal_id],
                                    detail=(
                                        f"{first.title} ends {first.ends_at}; {second.title} starts "
                                        f"{second.starts_at}; {int(gap_minutes)}min gap, {needed}min needed"
                                    ),
                                )
                            )

        if not conflicts:
            return {}
        saga_events = [
            SagaEvent(
                event_id=uuid.uuid4().hex,
                saga_id=state["saga_id"],
                sequence=_next_saga_sequence(state) + i,
                kind="conflict_detected",
                occurred_at=datetime.now(UTC),
                detail={"conflict_id": c.conflict_id, "kind": c.kind},
            )
            for i, c in enumerate(conflicts)
        ]
        return {"saga_log": saga_events}

    return conflict_check


def _route_from_conflict_check(state: ResortFlowState) -> str:
    recent = (state.get("saga_log") or [])[-5:]
    if any(e.kind == "conflict_detected" for e in recent):
        return N.PLANNER
    return N.PROPOSAL_ASSEMBLY


def make_proposal_assembly_node() -> Any:
    async def proposal_assembly(state: ResortFlowState) -> dict[str, Any]:
        held = [
            p for p in state.get("proposals", [])
            if (h := _find_hold_for_proposal(state, p)) is not None and h.status == HoldStatus.HELD
        ]
        if not held:
            return {
                "messages": [AIMessage(content="I couldn't find anything available for that request.")]
            }
        summary = "; ".join(f"{p.title} ({p.domain.value})" for p in held)
        update: dict[str, Any] = {"awaiting_approval": True, "approval_scope": summary}

        # FR-021: a domain named in the plan with no held proposal at all is
        # a partial itinerary, not a silent drop — log it here (the first
        # pass this becomes knowable) rather than only on a later replan.
        plan = state.get("plan")
        held_domains = {p.domain for p in held}
        if plan is not None:
            missing = [t.domain for t in plan.tasks if t.domain not in held_domains]
            if missing:
                update["saga_log"] = [
                    SagaEvent(
                        event_id=uuid.uuid4().hex, saga_id=state["saga_id"],
                        sequence=_next_saga_sequence(state) + i,
                        kind="partial_itinerary_proposed", domain=domain, occurred_at=datetime.now(UTC),
                        detail={"reason": "no_availability"},
                    )
                    for i, domain in enumerate(missing)
                ]
        return update

    return proposal_assembly


def _route_from_proposal_assembly(state: ResortFlowState) -> str:
    """A held proposal for at least one domain -> proceed to approval, even
    if that's only a partial itinerary (FR-021). Zero held proposals at all
    -> FanOut found nothing usable this pass; loop back to `PLANNER` (which
    owns `MAX_REPLAN_COUNT` and will escalate once the bound is hit) rather
    than silently ending the conversation on the first total failure — this
    is what makes spec.md's adversarial-input scenario (every domain
    unavailable) actually exhaust the bound instead of stopping after one
    attempt with no retry at all."""
    if state.get("awaiting_approval"):
        return N.HUMAN_APPROVAL
    plan = state.get("plan")
    if plan is not None and plan.tasks:
        return N.PLANNER
    return N.RESPOND


def make_human_approval_node() -> Any:
    """The single, cross-domain approval gate for a multi-domain itinerary —
    distinct from any individual domain subgraph's own `human_approval`
    (which `FanOut` never invokes; see module docstring).

    Returns a `Command(update=..., goto=...)` rather than a plain dict +
    separate `add_conditional_edges` registration: the approve/decline/modify
    decision has no natural home in the frozen `ResortFlowState` schema (adding a
    fourth scratch key for it would be exactly the kind of piecemeal
    amendment ADR-023 argues against for something with zero use beyond this
    one routing decision), and `Command.goto` lets a node carry its own
    routing outcome without inventing one."""

    async def human_approval(state: ResortFlowState) -> Any:
        from langgraph.types import Command

        decision = interrupt({"approval_scope": state.get("approval_scope")})
        if isinstance(decision, dict):
            action = decision.get("action") or ("approved" if decision.get("approved") else "declined")
        else:
            action = "approved" if decision else "declined"
        update = {"awaiting_approval": False, "approval_scope": None}
        goto = {"approved": N.COMMIT, "modified": N.PLANNER}.get(action, N.RELEASE)
        return Command(update=update, goto=goto)

    return human_approval
