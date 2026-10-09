"""T028 — Local scratch state for the wellness subgraph.

Mirrors `agents.dining.state` exactly (see that module's docstring for the
full rationale): `graph.state.ResortFlowState` and `graph.models.{Proposal, Hold,
Confirmation, Domain, SagaEvent, ToolFailure}` are frozen contracts — this
module imports them, never redefines them. `WellnessState` is a
*subgraph-local* superset of `ResortFlowState`: scratch keys here never become new
`ResortFlowState` keys (AGENTS.md hard rule via plan.md's "scratch data ... lives
in a local `TypedDict` extension").
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from graph.models import (  # noqa: F401 — re-exported for node modules' convenience
    Confirmation,
    Domain,
    Hold,
    Proposal,
    SagaEvent,
    ToolFailure,
)
from graph.state import ResortFlowState


class WellnessIntentParsed(TypedDict, total=False):
    """Raw structured extraction from the guest's free-text request, before
    any of it becomes a `Proposal`. Scratch-only — never persisted as a
    `ResortFlowState` key."""

    category: str | None
    """One of `spa`/`fitness`/`cabana`/`golf`, when resolvable."""
    date_str: str | None
    """ISO `YYYY-MM-DD`, when resolvable."""
    time_str: str | None
    """`HH:MM` (24h), when resolvable."""
    party_size: int | None
    """e.g. 2 for a couples massage."""
    practitioner_gender_preference: str | None
    ambiguous_fields: list[str]
    """Fields the parser could not confidently resolve — drives a
    clarification turn rather than a guess."""
    raw_text: str


class WellnessState(ResortFlowState):
    """Subgraph-local state: every `ResortFlowState` key, plus wellness-only
    scratch fields. Compiling the wellness `StateGraph` over this type (not
    `ResortFlowState` in isolation) is what lets `intent_parser` stash raw
    extraction results for `service_discovery`/`availability` to consume,
    without smuggling a new key into the frozen contract.

    `NotRequired` scratch fields default to absent on the first turn — no
    reducer is registered for them (LangGraph's default channel behavior:
    last-write-wins, single-writer-per-superstep, which is sufficient since
    exactly one wellness-subgraph node ever writes each of these)."""

    intent: NotRequired[WellnessIntentParsed | None]
    selected_proposal_id: NotRequired[str | None]
    """Which `state["proposals"]` entry this run of the subgraph is
    driving toward a hold/confirmation."""
    hold_attempt: NotRequired[int]
    """Monotonic per-proposal attempt counter, feeding the idempotency-key
    formula `resortflow:{session_id}:{proposal_id}:{attempt}`."""
    approval_rejected: NotRequired[bool]
    """Set by `human_approval` when the human declines a flagged conflict —
    routes straight to END rather than `confirm`."""
