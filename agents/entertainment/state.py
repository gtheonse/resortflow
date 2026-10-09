"""T022 — Local scratch state for the entertainment subgraph.

Mirrors `agents/dining/state.py` exactly in shape and rationale.
`graph.state.ResortFlowState` and `graph.models.{Confirmation, Domain, Hold,
Proposal, SagaEvent, ToolFailure}` are frozen contracts (see AGENTS.md hard
rules) — this module imports them, never redefines them.

`EntertainmentState` is a *subgraph-local* superset of `ResortFlowState`: LangGraph
allows a subgraph's own state schema to carry extra keys that are not part
of the shared, frozen key contract (they never get copied out when the
subgraph is mounted as a single node under `graph.nodes.ENTERTAINMENT` in a
parent graph — the parent only ever sees `ResortFlowState`'s own keys).
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


class EntertainmentIntentParsed(TypedDict, total=False):
    """Raw structured extraction from the guest's free-text request, before
    any of it becomes a `Proposal`. Scratch-only — never persisted as a
    `ResortFlowState` key."""

    classification: str | None
    """`music` / `theater` / `sports` / `comedy`, when resolvable."""
    date_str: str | None
    """ISO `YYYY-MM-DD`, when resolvable."""
    quantity: int | None
    """Number of tickets/seats — entertainment's analogue of dining's
    `party_size`."""
    section_pref: str | None
    ambiguous_fields: list[str]
    """Fields the parser could not confidently resolve — drives a
    clarification turn rather than a guess."""
    raw_text: str


class EntertainmentState(ResortFlowState):
    """Subgraph-local state: every `ResortFlowState` key, plus entertainment-only
    scratch fields. Compiling the entertainment `StateGraph` over this type
    (not `ResortFlowState` in isolation) is what lets `intent_parser` stash raw
    extraction results for `event_discovery`/`inventory_check` to consume,
    without smuggling a new key into the frozen contract.

    `NotRequired` scratch fields default to absent on the first turn — no
    reducer is registered for them (LangGraph's default channel behavior:
    last-write-wins, single-writer-per-superstep, which is sufficient since
    exactly one entertainment-subgraph node ever writes each of these).
    """

    intent: NotRequired[EntertainmentIntentParsed | None]
    selected_proposal_id: NotRequired[str | None]
    """Which `state["proposals"]` entry this run of the subgraph is
    driving toward a hold/confirmation. Kept as `selected_proposal_id`
    (not `selected_section_id`) for naming consistency with dining —
    `selected_section_id` is a separate field below, since the chosen
    section within an event is distinct information from which proposal is
    being driven toward commitment."""
    selected_section_id: NotRequired[str | None]
    """Which `Section` (from `check_inventory`'s response) was chosen for
    the selected proposal's event."""
    hold_attempt: NotRequired[int]
    """Monotonic per-proposal attempt counter, feeding the idempotency-key
    formula `resortflow:{session_id}:{proposal_id}:{attempt}`."""
    approval_rejected: NotRequired[bool]
    """Set by `human_approval` when the human declines — routes straight to
    END rather than `confirm`."""
