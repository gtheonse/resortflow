"""T045 — Local scratch state for the dining subgraph.

`graph.state.ResortFlowState` and `graph.models.{Proposal, Hold, Confirmation,
DietaryRequirement, DietaryRestriction, DietarySeverity, Domain, SagaEvent,
ToolFailure}` are frozen contracts from feature 001 (see AGENTS.md hard
rules and specs/004-dining-vertical-slice/plan.md). This module imports
them — it never redefines them.

`DiningState` is a *subgraph-local* superset of `ResortFlowState`: LangGraph
allows a subgraph's own state schema to carry extra keys that are not part
of the shared, 20-key contract (they simply never get copied out when the
subgraph is mounted as a single node under `graph.nodes.DINING` in a parent
graph — the parent only ever sees `ResortFlowState`'s own keys). This is exactly
the pattern plan.md calls for: "scratch data that doesn't belong in the
shared 20-key contract... lives in a local `TypedDict` extension inside
`agents/dining/`, never as a new `ResortFlowState` key."
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from graph.models import (  # noqa: F401 — re-exported for node modules' convenience
    Confirmation,
    DietaryRequirement,
    DietaryRestriction,
    DietarySeverity,
    Domain,
    Hold,
    Proposal,
    SagaEvent,
    ToolFailure,
)
from graph.state import ResortFlowState


class IntentParsed(TypedDict, total=False):
    """Raw structured extraction from the guest's free-text request, before
    any of it becomes a `Proposal`. Scratch-only — never persisted as a
    `ResortFlowState` key."""

    venue_pref: str | None
    cuisine: str | None
    party_size: int | None
    date_str: str | None
    """ISO `YYYY-MM-DD`, when resolvable."""
    time_str: str | None
    """`HH:MM` (24h), when resolvable."""
    occasion: str | None
    dietary: list[DietaryRequirement]
    ambiguous_fields: list[str]
    """Fields the parser could not confidently resolve — drives a
    clarification turn rather than a guess."""
    raw_text: str


class DietaryConflict(TypedDict):
    """One flagged mismatch between a `DietaryRequirement` and venue menu
    content, recorded for the approval-scope message and the eval set."""

    restriction: str
    severity: str
    menu_item: str
    chunk_id: str
    source_uri: str


class DiningState(ResortFlowState):
    """Subgraph-local state: every `ResortFlowState` key, plus dining-only
    scratch fields. Compiling the dining `StateGraph` over this type (not
    `ResortFlowState` in isolation) is what lets `intent_parser` stash raw
    extraction results for `venue_discovery`/`allergen_check` to consume,
    without smuggling a new key into the frozen contract.

    `NotRequired` scratch fields default to absent on the first turn — no
    reducer is registered for them (LangGraph's default channel behavior:
    last-write-wins, single-writer-per-superstep, which is sufficient since
    exactly one dining-subgraph node ever writes each of these).
    """

    intent: NotRequired[IntentParsed | None]
    selected_proposal_id: NotRequired[str | None]
    """Which `state["proposals"]` entry this run of the subgraph is
    driving toward a hold/confirmation."""
    dietary_conflicts: NotRequired[list[DietaryConflict]]
    waitlisted: NotRequired[bool]
    waitlist_id: NotRequired[str | None]
    hold_attempt: NotRequired[int]
    """Monotonic per-proposal attempt counter, feeding the idempotency-key
    formula `resortflow:{session_id}:{proposal_id}:{attempt}`."""
    approval_rejected: NotRequired[bool]
    """Set by `human_approval` when the human declines a flagged dietary
    conflict — routes straight to END rather than `confirm`."""
    policy_blocked: NotRequired[bool]
    """Feature 006 (ADR-029, FR-006): set by `allergen_check` when either
    the existing menu-content conflict check or the new
    `policy.dietary.check_dietary_fidelity` call finds a blocking mismatch.
    Always explicitly written (True or False) on every `allergen_check` run
    — unlike this file's other `NotRequired` scratch fields, this one must
    never be allowed to persist stale from an earlier attempt, since FR-006
    requires the block to clear the moment the structured record is
    corrected and the check re-runs. `route_after_hold_placement` checks
    this before `awaiting_approval` and routes straight to END — this block
    is never approvable, so it never reaches `human_approval`."""
