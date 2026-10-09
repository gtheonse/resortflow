"""T049 — Local scratch state for the gaming subgraph.

`graph.state.ResortFlowState` and `graph.models.{Domain, ToolFailure}` are frozen
contracts from feature 001 (see AGENTS.md hard rules and
specs/005-multi-agent-orchestration/data-model.md). This module imports
them — it never redefines them.

`GamingState` is a *subgraph-local* superset of `ResortFlowState`, mirroring
`agents.dining.state.DiningState`'s pattern: scratch data that doesn't
belong in the shared 20-key contract lives here, never as a new `ResortFlowState`
key.

Deliberately absent, unlike every other domain's local state: `hold_attempt`
and `approval_rejected`. Gaming is read-only by construction (Constitution
Principle II) — there is no hold to attempt and nothing to approve or
reject, so this state shape carries no fields implying either exists. See
data-model.md's "GamingState" note: "No hold/approval fields — gaming is
read-only... enforced by the absence of mutating tools, not by state shape
alone, but the state shape reflects it too: there is no hold_attempt because
there is no hold."
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from graph.models import (  # noqa: F401 — re-exported for node modules' convenience
    Domain,
    ToolFailure,
)
from graph.state import ResortFlowState

GamingIntentKind = Literal["rg_status", "loyalty_status", "floor_games"]


class GamingIntentParsed(TypedDict, total=False):
    """Raw structured extraction from the guest's free-text request. Scratch
    only — never persisted as a `ResortFlowState` key.

    No `date_str`/`party_size` fields exist here (unlike dining/wellness'
    equivalents) — gaming lookups aren't scheduled; every one of the three
    read-only tools answers "what is true right now.\""""

    intent_kind: GamingIntentKind
    game_type: str | None
    """Filter for `list_casino_floor`, e.g. `"blackjack"`/`"poker"`/
    `"slots"`/`"roulette"`/`"craps"`/`"baccarat"`. Only meaningful when
    `intent_kind == "floor_games"`."""
    ambiguous_fields: list[str]
    raw_text: str


class GamingState(ResortFlowState):
    """Subgraph-local state: every `ResortFlowState` key, plus gaming-only scratch.
    Compiling the gaming `StateGraph` over this type (not `ResortFlowState` in
    isolation) is what lets `intent_parser` stash raw extraction results for
    `lookup` to consume, without smuggling a new key into the frozen
    contract.

    `NotRequired` scratch fields default to absent on the first turn — no
    reducer is registered for them (LangGraph's default channel behavior:
    last-write-wins, single-writer-per-superstep, which is sufficient since
    exactly one gaming-subgraph node ever writes this key).
    """

    intent: NotRequired[GamingIntentParsed | None]
