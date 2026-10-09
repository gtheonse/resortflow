"""T044 — Local scratch state for the stay subgraph.

Mirrors `agents.dining.state` exactly (see that module's docstring for the
full rationale): `graph.state.ResortFlowState` and `graph.models.{Confirmation,
Domain, Hold, Proposal, SagaEvent, ToolFailure}` are frozen contracts — this
module imports them, never redefines them. `StayState` is a *subgraph-local*
superset of `ResortFlowState`: scratch keys here never become new `ResortFlowState` keys
(AGENTS.md hard rule via plan.md's "scratch data ... lives in a local
`TypedDict` extension").
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


class StayIntentParsed(TypedDict, total=False):
    """Raw structured extraction from the guest's free-text request, before
    any of it becomes a `Proposal`. Scratch-only — never persisted as a
    `ResortFlowState` key."""

    room_class: str | None
    """One of `standard`/`deluxe`/`suite`, when resolvable."""
    date_str: str | None
    """ISO `YYYY-MM-DD` arrival date, when resolvable."""
    nights: int | None
    """Length of stay in nights. Defaults to 1 when not stated — never left
    `None` once extraction runs, since a room hold always needs a departure
    date computed from `date_str + nights`."""
    party_size: int | None
    """Adult occupancy, e.g. 2 for a couple."""
    arrival_assumed: bool
    """Feature 011 (FR-018): `room_discovery` used tomorrow because the guest gave no arrival date; the approval
    interrupt says so, since a nested subgraph's own messages are not visible to the guest while it is paused."""
    ambiguous_fields: list[str]
    """Fields the parser could not confidently resolve — drives a
    clarification turn rather than a guess."""
    raw_text: str


class StayState(ResortFlowState):
    """Subgraph-local state: every `ResortFlowState` key, plus stay-only scratch
    fields. Compiling the stay `StateGraph` over this type (not `ResortFlowState`
    in isolation) is what lets `intent_parser` stash raw extraction results
    for `room_discovery`/`hold_placement` to consume, without smuggling a
    new key into the frozen contract.

    `NotRequired` scratch fields default to absent on the first turn — no
    reducer is registered for them (LangGraph's default channel behavior:
    last-write-wins, single-writer-per-superstep, which is sufficient since
    exactly one stay-subgraph node ever writes each of these)."""

    intent: NotRequired[StayIntentParsed | None]
    selected_proposal_id: NotRequired[str | None]
    """Which `state["proposals"]` entry this run of the subgraph is driving
    toward a hold/confirmation."""
    hold_attempt: NotRequired[int]
    """Monotonic per-proposal attempt counter, feeding the idempotency-key
    formula `resortflow:{session_id}:{proposal_id}:{attempt}`."""
    approval_rejected: NotRequired[bool]
    """Set by `human_approval` when the human declines — routes straight to
    END rather than `confirm`."""
