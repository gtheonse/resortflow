"""Interface Contract: `ResortFlowState` — the canonical conversation state.

Permanent home. Every domain subgraph reads and writes this state.
`ResortFlowState` is a `TypedDict` (not a Pydantic `BaseModel`) because LangGraph
binds reducers to individual channels via `Annotated` metadata — a
`BaseModel` state has no per-channel reducer mechanism (research.md item 4).

This is a frozen-contract file (AGENTS.md hard rule; the working agreement's
"contract changes go through a single integration commit" rule). The four
tasks that build it (T012-T015) all edit this one file and run strictly
sequentially for that reason.

T012 — `merge_proposals` (FR-002)
T013 — `merge_holds` (FR-003, FR-004)
T014 — `append_only`, `dedupe_by_id` (FR-005, FR-006)
T015 — the `ResortFlowState` TypedDict + `MAX_REPLAN_COUNT` (FR-001, FR-007,
        FR-008, FR-011, FR-012)

Every reducer below satisfies three properties (contracts/state-schema.md),
each covered by `tests/test_reducers.py`:

1. Accepts an absent left value — the first write to a channel in a fresh
   conversation must not error.
2. Idempotent under replay — applying an identical right-hand contribution
   twice leaves the result identical to applying it once. LangGraph resume
   replays steps, so a non-idempotent reducer double-books on every resume.
3. Loses nothing contributed in the same superstep — three concurrent
   writers must all be represented in the result.
"""

from __future__ import annotations

from typing import Annotated, Final, TypedDict, TypeVar

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from graph.models import TERMINAL_HOLD_STATUSES as _TERMINAL_HOLD_STATUSES
from graph.models import (
    Chunk,
    Citation,
    Confirmation,
    Hold,
    Plan,
    PolicyFlag,
    Proposal,
    RoutingDecision,
    SagaEvent,
    ToolFailure,
    TurnBaseline,
)

T = TypeVar("T")

MAX_REPLAN_COUNT: Final[int] = 3
"""Hard bound on the planner -> fan-out -> conflict-check -> replan cycle
(research.md item 7). A node incrementing `replan_count` past this bound
must set `escalate = True` rather than continue the cycle. Enforced by
`graph.supervisor`'s `PLANNER` node (feature 005)."""

MAX_CLARIFICATION_COUNT: Final[int] = 2
"""Hard bound on the `IntentRouter <-> Clarify` cycle (ADR-024, feature 005).
Breach sets `escalate = True`, `escalate_reason = "clarification_bound"`, and
routes to `HUMAN_HANDOFF` — see `contracts/graph-topology-v005.md`."""


# --- T012: merge_proposals (FR-002) -----------------------------------------


def merge_proposals(
    left: list[Proposal] | None, right: list[Proposal]
) -> list[Proposal]:
    """Merge by `proposal_id`. On a collision the right (newer) value wins.
    Items new to the right side are appended, preserving left-side ordering
    for everything already present."""
    merged: dict[str, Proposal] = {p.proposal_id: p for p in (left or [])}
    order = list(merged.keys())
    for proposal in right:
        if proposal.proposal_id not in merged:
            order.append(proposal.proposal_id)
        merged[proposal.proposal_id] = proposal
    return [merged[pid] for pid in order]


# --- T013: merge_holds (FR-003, FR-004) -------------------------------------


def merge_holds(left: list[Hold] | None, right: list[Hold]) -> list[Hold]:
    """Merge by `idempotency_key` — never `hold_id`, which is `None` until
    the vendor responds and therefore cannot identify an in-flight attempt.

    Terminal-state precedence: if the left value's `status` is one of
    `confirmed`, `released`, `expired`, `orphan_released`, the right value
    MUST NOT overwrite it with `pending` or `held`. Resume replays steps, so
    arrival order carries no information (data-model.md's Hold state
    machine)."""
    merged: dict[str, Hold] = {h.idempotency_key: h for h in (left or [])}
    order = list(merged.keys())
    for hold in right:
        existing = merged.get(hold.idempotency_key)
        if existing is not None and existing.status in _TERMINAL_HOLD_STATUSES:
            # Terminal never regresses, regardless of what the (possibly
            # replayed, possibly stale) right-hand write claims.
            continue
        if hold.idempotency_key not in merged:
            order.append(hold.idempotency_key)
        merged[hold.idempotency_key] = hold
    return [merged[key] for key in order]


# --- T014: append_only, dedupe_by_id (FR-005, FR-006) -----------------------


def append_only(left: list[T] | None, right: list[T]) -> list[T]:
    """Concatenation. Used for `confirmations`, `failures`, `saga_log`.
    Never rewrites an existing element."""
    return [*(left or []), *right]


def dedupe_by_id(left: list[Chunk] | None, right: list[Chunk]) -> list[Chunk]:
    """Deduplicate by `chunk_id`. First occurrence wins, so relevance rank
    order from the RRF fusion step survives the merge."""
    seen: dict[str, Chunk] = {c.chunk_id: c for c in (left or [])}
    order = list(seen.keys())
    for chunk in right:
        if chunk.chunk_id not in seen:
            seen[chunk.chunk_id] = chunk
            order.append(chunk.chunk_id)
    return [seen[cid] for cid in order]


# --- T015: ResortFlowState ----------------------------------------------------------


class ResortFlowState(TypedDict):
    """The single shared record of an in-flight guest request. Twenty keys
    (data-model.md's "`ResortFlowState` — the frozen key set"). Adding, removing
    or renaming a key is a breaking change requiring an ADR — never a
    routine edit (FR-021).

    `tenant_id`, `guest_id`, `session_id`, `jurisdiction` are set once at
    conversation entry from a validated token claim (FR-011). No reducer,
    node, or downstream code may derive or mutate them from model output —
    that property depends on which node writes them, and no nodes exist
    until feature 005 (spec.md Assumptions, "the provenance half")."""

    # Identity and tenancy — set once at entry, never model-derived.
    tenant_id: str
    guest_id: str
    session_id: str
    jurisdiction: str
    entitlements: list[str]
    saga_id: str
    """Deviation from docs/ResortFlow-Enterprise-Multi-Agent-Plan.md §5 — see
    plan.md's "Recorded deviations" and ADR-019. Joins conversation state to
    ledger rows; spans multiple turns and multiple traces."""

    # Conversation history.
    messages: Annotated[list[AnyMessage], add_messages]
    """Windowing deferred to feature 005 (plan.md's "Recorded deviations")."""

    # Planning.
    plan: Plan | None
    replan_count: int
    """Hard bound `MAX_REPLAN_COUNT`, then escalate."""

    # Domain results.
    proposals: Annotated[list[Proposal], merge_proposals]
    holds: Annotated[list[Hold], merge_holds]
    confirmations: Annotated[list[Confirmation], append_only]
    failures: Annotated[list[ToolFailure], append_only]

    # Grounding.
    retrieved: Annotated[list[Chunk], dedupe_by_id]
    citations: list[Citation]

    # Control.
    awaiting_approval: bool
    approval_scope: str | None
    policy_flags: Annotated[list[PolicyFlag], append_only]
    """Append-only (feature 006 amendment, ADR-029). Was unannotated since
    feature 001 with zero writers; concurrent domain subgraphs (feature 005
    fan-out) can each raise a flag in the same superstep, so this channel
    needs to accumulate rather than last-write-win — the same reducer
    already proven for `confirmations`/`failures`/`saga_log`."""
    escalate: bool
    saga_log: Annotated[list[SagaEvent], append_only]
    """Append-only; mirrored to Postgres so compensation survives process
    death."""

    # --- Feature 005 amendment (ADR-023, additive-only) ---------------------
    graph_version: str
    """Set once at thread start from a build-time constant
    (`graph.supervisor.GRAPH_VERSION`); compared on resume — a major-version
    mismatch releases outstanding holds and asks the guest to restart
    (`ResortFlow-HITL-Durability-State.md` §8 mitigation #1/#5,
    `contracts/state-schema-amendment-005.md`)."""
    clarification_count: int
    """Default 0. Incremented by `CLARIFY` (ADR-024) each time it asks a
    follow-up question. Bounded by `MAX_CLARIFICATION_COUNT`."""
    escalate_reason: str | None
    """Default `None`. One of `{"replan_bound", "clarification_bound",
    "compensation_failed", "distress_signal", "modify_request"}`, set
    exactly once, at the point escalation fires
    (`contracts/state-schema-amendment-005.md`; `"distress_signal"` added
    feature 006, ADR-036 — `POLICY_PREFLIGHT` routes to `HUMAN_HANDOFF` on a
    gambling-distress signal, confident or ambiguous; `"modify_request"`
    added feature 010, ADR-056 — a `modify`/cancel request is recognised and
    handed off, never performed)."""

    # --- Feature 010 amendment (ADR-056, additive-only) ---------------------
    routing: RoutingDecision | None
    """Default `None`. Written once per turn, by `INTENT_ROUTER` only — no
    reducer (single writer per superstep is sufficient). `None` on a
    pre-010 checkpoint and on the very first routed turn before
    `INTENT_ROUTER` runs. Read by `_route_from_intent_router`,
    `DETERMINISTIC`, `PLANNER`, `BOOKING_LOOKUP`, `MODIFY_REQUEST`, and the
    next turn's `INTENT_ROUTER` (one-turn follow-up carry-over, FR-008a).
    See `contracts/state-schema-amendment-010.md`."""
    turn_baseline: TurnBaseline | None
    """Default `None`. Written once per turn, by `INTENT_ROUTER` only — no
    reducer. `None` on a pre-010 checkpoint's first post-upgrade turn
    (`graph.bookings.turn_outcomes` then returns `None` rather than
    guessing, FR-031; see `contracts/turn-replies.md`). Read by the reply
    composer (`RESPOND` / `DETERMINISTIC`) and the next turn's carry-over
    eligibility check."""
