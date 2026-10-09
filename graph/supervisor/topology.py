"""Graph assembly only: node registration and edge wiring (`contracts/graph-topology-v005.md`)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from graph import nodes as N
from graph.ledger import HoldLedger, NullHoldLedger
from graph.models import (
    Domain,
)
from graph.routing.classifier import RoutingClassifier
from graph.state import ResortFlowState
from graph.supervisor.common import DEFAULT_TOOLS_FACTORY
from graph.supervisor.deterministic import make_deterministic_node
from graph.supervisor.intent import (
    _route_from_clarify,
    _route_from_intent_router,
    make_clarify_node,
    make_intent_router_node,
)
from graph.supervisor.planning import (
    _FANOUT_DOMAIN_NODES,
    _FANOUT_DOMAINS,
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
from graph.supervisor.respond import make_respond_node
from graph.supervisor.saga import (
    _route_from_commit_verify,
    _route_from_recovery,
    make_commit_node,
    make_commit_verify_node,
    make_recovery_node,
    make_release_node,
)
from graph.supervisor.session import _route_from_entry, make_entry_node
from observability.telemetry import wrap_node

# ============================================================================
# Graph assembly
# ============================================================================


def build_supervisor_graph(
    *,
    tools_factory: dict[Domain, Callable[..., Any]] | None = None,
    ledger: HoldLedger | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    retriever_factory: Callable[[], Any] | None = None,
    trace: bool = False,
    classifier: RoutingClassifier | None = None,
) -> StateGraph[ResortFlowState, Any, Any, Any]:
    """Assemble (but do not compile) the supervisor `StateGraph` per
    `contracts/graph-topology-v005.md`. `FANOUT_JOIN` here is an internal
    synchronization point, not a name in that contract's diagram — LangGraph
    fans in `ConflictCheck` automatically from every domain's own edge, so no
    separate join node is strictly required, but naming it keeps the
    dependency graph below readable. `checkpointer` is forwarded only to
    `DETERMINISTIC`'s inner standalone-subgraph compilation (see
    `make_deterministic_node`'s docstring) — the outer graph's own
    checkpointer is supplied separately, at `.compile()` time, same as every
    other LangGraph app in this project. `classifier` (feature 010) is
    forwarded only to `INTENT_ROUTER`'s tier-2 call — injectable so tests
    can supply a fake `RoutingClassifier` without touching Azure
    credentials; `None` builds the real credential-fallback default
    (`build_default_classifier`)."""
    tools_factory = tools_factory or DEFAULT_TOOLS_FACTORY
    ledger = ledger or NullHoldLedger()

    def _wrap(name: str, fn: Any) -> Any:
        # feature 007 (ADR-041): first-ever supervisor-level span emission,
        # via observability.telemetry rather than a node-local tracer.
        return wrap_node(name, fn, domain="supervisor") if trace else fn

    graph = StateGraph(ResortFlowState)

    ENTRY = "_entry"
    graph.add_node(ENTRY, _wrap(ENTRY, make_entry_node()))
    graph.add_node(N.INTENT_ROUTER, _wrap(N.INTENT_ROUTER, make_intent_router_node(classifier)))
    graph.add_node(N.CLARIFY, _wrap(N.CLARIFY, make_clarify_node()))
    graph.add_node(
        N.KNOWLEDGE_QA, _wrap(N.KNOWLEDGE_QA, make_knowledge_qa_node(tools_factory, retriever_factory))
    )
    graph.add_node(N.BOOKING_LOOKUP, _wrap(N.BOOKING_LOOKUP, make_booking_lookup_node(tools_factory)))
    graph.add_node(N.DECLINE, _wrap(N.DECLINE, make_decline_node(tools_factory)))
    graph.add_node(N.SMALLTALK, _wrap(N.SMALLTALK, make_smalltalk_node(tools_factory)))
    graph.add_node(N.BROWSE, _wrap(N.BROWSE, make_browse_node(tools_factory)))
    graph.add_node(N.MODIFY_REQUEST, _wrap(N.MODIFY_REQUEST, make_modify_request_node()))
    graph.add_node(
        N.DETERMINISTIC, _wrap(N.DETERMINISTIC, make_deterministic_node(tools_factory, checkpointer))
    )
    graph.add_node(N.SUPERVISOR, _wrap(N.SUPERVISOR, make_supervisor_node()))
    graph.add_node(N.POLICY_PREFLIGHT, _wrap(N.POLICY_PREFLIGHT, make_policy_preflight_node()))
    graph.add_node(N.BLOCKED, _wrap(N.BLOCKED, make_blocked_node()))
    graph.add_node(N.PLANNER, _wrap(N.PLANNER, make_planner_node()))
    for domain in _FANOUT_DOMAINS:
        graph.add_node(
            domain.value, _wrap(domain.value, make_fanout_domain_node(domain, tools_factory, ledger))
        )
    graph.add_node(N.CONFLICT_CHECK, _wrap(N.CONFLICT_CHECK, make_conflict_check_node()))
    graph.add_node(N.PROPOSAL_ASSEMBLY, _wrap(N.PROPOSAL_ASSEMBLY, make_proposal_assembly_node()))
    graph.add_node(
        N.HUMAN_APPROVAL, _wrap(N.HUMAN_APPROVAL, make_human_approval_node()),
        destinations={N.COMMIT: N.COMMIT, N.PLANNER: N.PLANNER, N.RELEASE: N.RELEASE},
    )
    graph.add_node(N.COMMIT, _wrap(N.COMMIT, make_commit_node(tools_factory, ledger)))
    graph.add_node(N.RELEASE, _wrap(N.RELEASE, make_release_node(tools_factory, ledger)))
    graph.add_node(N.COMMIT_VERIFY, _wrap(N.COMMIT_VERIFY, make_commit_verify_node(tools_factory)))
    graph.add_node(N.RECOVERY, _wrap(N.RECOVERY, make_recovery_node(tools_factory, ledger)))
    graph.add_node(N.POLICY_POSTFLIGHT, _wrap(N.POLICY_POSTFLIGHT, make_policy_postflight_node()))
    graph.add_node(N.OFFER_AGENT, _wrap(N.OFFER_AGENT, make_offer_agent_node(tools_factory)))
    graph.add_node(N.RESPOND, _wrap(N.RESPOND, make_respond_node()))
    graph.add_node(N.HUMAN_HANDOFF, _wrap(N.HUMAN_HANDOFF, make_human_handoff_node()))

    graph.add_edge(START, ENTRY)
    graph.add_conditional_edges(
        ENTRY, _route_from_entry,
        {N.RELEASE: N.RELEASE, N.HUMAN_HANDOFF: N.HUMAN_HANDOFF, N.POLICY_PREFLIGHT: N.POLICY_PREFLIGHT},
    )
    graph.add_conditional_edges(
        N.POLICY_PREFLIGHT,
        _route_from_policy_preflight,
        {N.INTENT_ROUTER: N.INTENT_ROUTER, N.HUMAN_HANDOFF: N.HUMAN_HANDOFF},
    )
    graph.add_conditional_edges(
        N.INTENT_ROUTER, _route_from_intent_router,
        {
            N.KNOWLEDGE_QA: N.KNOWLEDGE_QA,
            N.DETERMINISTIC: N.DETERMINISTIC,
            N.SUPERVISOR: N.SUPERVISOR,
            N.CLARIFY: N.CLARIFY,
            N.BOOKING_LOOKUP: N.BOOKING_LOOKUP,
            N.DECLINE: N.DECLINE,
            N.SMALLTALK: N.SMALLTALK,
            N.MODIFY_REQUEST: N.MODIFY_REQUEST,
            N.BROWSE: N.BROWSE,
        },
    )
    graph.add_edge(N.MODIFY_REQUEST, N.HUMAN_HANDOFF)
    graph.add_edge(N.DECLINE, END)
    graph.add_edge(N.SMALLTALK, END)
    graph.add_conditional_edges(
        N.KNOWLEDGE_QA, _route_from_knowledge_qa, {END: END, N.CLARIFY: N.CLARIFY}
    )
    graph.add_conditional_edges(
        N.BOOKING_LOOKUP, _route_from_booking_lookup,
        {END: END, N.DETERMINISTIC: N.DETERMINISTIC, N.SUPERVISOR: N.SUPERVISOR},
    )
    graph.add_conditional_edges(
        N.BROWSE, _route_from_browse, {END: END, N.DETERMINISTIC: N.DETERMINISTIC, N.SUPERVISOR: N.SUPERVISOR}
    )
    graph.add_conditional_edges(
        N.CLARIFY, _route_from_clarify, {N.INTENT_ROUTER: N.INTENT_ROUTER, N.HUMAN_HANDOFF: N.HUMAN_HANDOFF}
    )
    graph.add_edge(N.DETERMINISTIC, END)
    graph.add_edge(N.SUPERVISOR, N.PLANNER)
    graph.add_edge(N.BLOCKED, N.RESPOND)
    graph.add_conditional_edges(
        N.PLANNER, _route_from_planner,
        {N.HUMAN_HANDOFF: N.HUMAN_HANDOFF, **{d: d for d in _FANOUT_DOMAIN_NODES}},
    )
    for domain in _FANOUT_DOMAINS:
        graph.add_edge(domain.value, N.CONFLICT_CHECK)
    graph.add_conditional_edges(
        N.CONFLICT_CHECK, _route_from_conflict_check, {N.PLANNER: N.PLANNER, N.PROPOSAL_ASSEMBLY: N.PROPOSAL_ASSEMBLY}
    )
    graph.add_conditional_edges(
        N.PROPOSAL_ASSEMBLY, _route_from_proposal_assembly,
        {N.HUMAN_APPROVAL: N.HUMAN_APPROVAL, N.PLANNER: N.PLANNER, N.RESPOND: N.RESPOND},
    )
    graph.add_edge(N.COMMIT, N.COMMIT_VERIFY)
    graph.add_conditional_edges(
        N.COMMIT_VERIFY, _route_from_commit_verify,
        {N.RECOVERY: N.RECOVERY, N.POLICY_POSTFLIGHT: N.POLICY_POSTFLIGHT, N.HUMAN_HANDOFF: N.HUMAN_HANDOFF},
    )
    graph.add_conditional_edges(
        N.RECOVERY, _route_from_recovery, {N.HUMAN_HANDOFF: N.HUMAN_HANDOFF, N.POLICY_POSTFLIGHT: N.POLICY_POSTFLIGHT}
    )
    graph.add_edge(N.POLICY_POSTFLIGHT, N.OFFER_AGENT)
    graph.add_edge(N.OFFER_AGENT, N.RESPOND)
    graph.add_edge(N.RELEASE, N.RESPOND)
    graph.add_edge(N.RESPOND, END)
    graph.add_edge(N.HUMAN_HANDOFF, END)

    return graph


def compile_supervisor_graph(
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    tools_factory: dict[Domain, Callable[..., Any]] | None = None,
    ledger: HoldLedger | None = None,
    retriever_factory: Callable[[], Any] | None = None,
    trace: bool = False,
    classifier: RoutingClassifier | None = None,
) -> Any:
    graph = build_supervisor_graph(
        tools_factory=tools_factory, ledger=ledger, checkpointer=checkpointer,
        retriever_factory=retriever_factory, trace=trace, classifier=classifier,
    )
    return graph.compile(checkpointer=checkpointer)
