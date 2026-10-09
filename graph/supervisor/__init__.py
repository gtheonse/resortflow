"""T014-T021, T032, T065, T069 — the multi-domain orchestration supervisor.

Compiles the `StateGraph(ResortFlowState)` topology `contracts/graph-topology-v005.md`
specifies, over the shared, frozen `ResortFlowState` — no supervisor-specific state
extension is needed (`data-model.md`'s "Supervisor-level state" section: "No new
`ResortFlowState` key is needed for the supervisor's own bookkeeping beyond `plan` and
`replan_count`, both already frozen").

## Two paths through this module, not one

Feature 004 proved a *standalone* domain subgraph shape (dining: intent -> discover ->
availability -> hold -> approve -> confirm, one domain, one approval). This feature adds
five of those (only dining/entertainment/wellness exist as of this commit; stay/gaming
follow in a later task) — and *also* a genuinely different, *cross-domain* saga shape: one
plan spanning N domains gets ONE approval and ONE sequential, ordered commit across all of
them together (`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §6). These are not the same
graph run twice — they are two distinct consumers of the same domain building blocks:

- `DETERMINISTIC`: a single-domain, unambiguous request is handed to that domain's own
  *complete* compiled subgraph (`agents.dining.graph.compile_dining_graph` and
  its entertainment/wellness/stay/gaming equivalents) — the exact, already-tested Phase 4
  shape, own approval and own confirm included. This is what closes User Stories 3/4.
- `FANOUT`: a multi-domain request never invokes those compiled subgraphs. It reuses each
  domain's individual node *functions* (`agents.dining.nodes.*`, plus the
  `make_*_node` factories) directly, running only the propose-and-hold prefix of each
  domain's flow, and stops there — the cross-domain `HumanApproval` / `Commit` /
  `CommitVerify` / `Recovery` / `Release` nodes below own the approval and the
  saga-ordered, independently-verified commit across every domain in the plan at once.
  This is what closes User Stories 1/2/5 and is the point of this whole feature.

## Deliberate scope boundaries in this implementation

- `POLICY_PREFLIGHT`/`POLICY_POSTFLIGHT` are pass-throughs (Constitution Principle IV is
  Phase 6's), matching `contracts/graph-topology-v005.md`'s own "what this contract does
  not cover."
- `PLANNER`'s domain-decomposition is deterministic keyword matching, not an LLM call —
  a defensible, disclosed choice consistent with Principle I ("LLMs only where intent is
  genuinely unbounded"; which domains a sentence mentions is not that) and it keeps this
  module fully testable without live model credentials, mirroring
  `agents/dining/nodes/intent_parser.py`'s own rule-based-fallback precedent.
- `RECOVERY`'s semantic-alternative search (T041, generalized T072 per
  `/speckit-converge`'s C1 finding against FR-013) covers all four mutating domains:
  dining, entertainment, wellness, and stay each re-run their own discover/availability
  path for a nearby slot/venue/room and propose it, preserving every already-committed
  piece, before ever escalating. A domain failure that finds no alternative still
  escalates with `escalate_reason="compensation_failed"` (ADR-027's dead-letter path)
  rather than looping.
- `COMMIT_VERIFY` (T019/T074) performs a genuine independent vendor round-trip only for
  entertainment, whose `Confirmation.confirmation_id` is literally the vendor's
  `order_id` (`get_order_status(order_id=...)` is a direct, unambiguous lookup). Dining's
  `Confirmation.confirmation_id` is the guest-facing confirmation *code*, not the
  vendor's `reservation_id` needed by `check_dining_status(kind="reservation", ...)` —
  recovering that id would mean widening the frozen `Confirmation` model, an ADR-worthy
  change out of scope here. Wellness and stay publish no status-check tool in their
  gateway servers at all (the same gap their own `nodes/confirm.py` modules already
  disclose for resume-revalidation). For those three, verification stays structural: a
  hold that never reached `CONFIRMED` in `Commit`'s own pass is, by definition,
  unverified — there is no further vendor round-trip available to perform.
"""

# ruff: noqa: F401  (facade: re-exports are the point, incl. private names tests pin)
from __future__ import annotations

from graph.supervisor.common import (
    _REGISTRY,
    _RELEVANCE_FLOOR,
    DEFAULT_TOOLS_FACTORY,
    GRAPH_VERSION,
    _detect_domains,
    _find_hold_for_proposal,
    _latest_human_text,
    _load_relevance_floor,
    _next_saga_sequence,
)
from graph.supervisor.deterministic import (
    _compile_standalone_graph,
    _deterministic_core,
    make_deterministic_node,
)
from graph.supervisor.intent import (
    _CLARIFY_QUESTION,
    _route_from_clarify,
    _route_from_intent_router,
    make_clarify_node,
    make_intent_router_node,
)
from graph.supervisor.planning import (
    _FANOUT_DOMAIN_NODES,
    _FANOUT_DOMAINS,
    _OWN_APPROVAL_KEYS,
    _as_aware,
    _route_from_conflict_check,
    _route_from_planner,
    _route_from_proposal_assembly,
    make_conflict_check_node,
    make_fanout_domain_node,
    make_human_approval_node,
    make_planner_node,
    make_proposal_assembly_node,
    make_supervisor_node,
)
from graph.supervisor.policy_gates import (
    _BLOCK_REASON_MESSAGES,
    _apply_offer_agent,
    _apply_policy_postflight,
    _route_from_policy_preflight,
    make_blocked_node,
    make_human_handoff_node,
    make_offer_agent_node,
    make_policy_postflight_node,
    make_policy_preflight_node,
)
from graph.supervisor.read_paths import (
    _route_from_booking_lookup,
    _route_from_browse,
    _route_from_knowledge_qa,
    make_booking_lookup_node,
    make_browse_node,
    make_decline_node,
    make_knowledge_qa_node,
    make_modify_request_node,
    make_smalltalk_node,
)
from graph.supervisor.respond import (
    _compose_respond_message,
    make_respond_node,
)
from graph.supervisor.saga import (
    _route_from_commit_verify,
    _route_from_recovery,
    make_commit_node,
    make_commit_verify_node,
    make_recovery_node,
    make_release_node,
)
from graph.supervisor.session import (
    _compute_previous_turn,
    _compute_turn_baseline,
    _route_from_entry,
    make_entry_node,
    new_session_state,
)
from graph.supervisor.topology import (
    build_supervisor_graph,
    compile_supervisor_graph,
)

__all__ = [
    "DEFAULT_TOOLS_FACTORY",
    "GRAPH_VERSION",
    "build_supervisor_graph",
    "compile_supervisor_graph",
    "make_blocked_node",
    "make_booking_lookup_node",
    "make_browse_node",
    "make_clarify_node",
    "make_commit_node",
    "make_commit_verify_node",
    "make_conflict_check_node",
    "make_decline_node",
    "make_deterministic_node",
    "make_entry_node",
    "make_fanout_domain_node",
    "make_human_approval_node",
    "make_human_handoff_node",
    "make_intent_router_node",
    "make_knowledge_qa_node",
    "make_modify_request_node",
    "make_offer_agent_node",
    "make_planner_node",
    "make_policy_postflight_node",
    "make_policy_preflight_node",
    "make_proposal_assembly_node",
    "make_recovery_node",
    "make_release_node",
    "make_respond_node",
    "make_smalltalk_node",
    "make_supervisor_node",
    "new_session_state",
]
