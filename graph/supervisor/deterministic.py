"""The `DETERMINISTIC` path: dispatch a single-domain request to that domain's own standalone subgraph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from graph.models import (
    Domain,
    RequestKind,
)
from graph.state import ResortFlowState
from graph.supervisor.common import (
    _REGISTRY,
    DEFAULT_TOOLS_FACTORY,
    _detect_domains,
    _latest_human_text,
)
from graph.supervisor.policy_gates import _apply_offer_agent, _apply_policy_postflight
from graph.supervisor.respond import _compose_respond_message
from graph.supervisor.subgraph_bridge import run_in_subthread


def _compile_standalone_graph(domain: Domain, tools: Any, checkpointer: BaseCheckpointSaver[Any] | None = None) -> Any:
    """Compile `domain`'s own complete, already-tested standalone subgraph
    (own approval, own confirm — the Phase 4 shape). `checkpointer` is the
    *supervisor's own* checkpointer, threaded through so a single-domain
    request that hits that domain's own interrupt (e.g. dining's
    allergen-conflict approval) can actually be resumed on a later turn
    rather than silently losing state — without this, `DETERMINISTIC`
    compiling a fresh, checkpointer-less graph on every invocation would
    make any standalone interrupt unrecoverable. `None` falls back to
    LangGraph's own ephemeral default, which is fine for a request that
    never interrupts (the common case) and matches every domain's own
    `compile_*_graph(tools)` no-checkpointer default."""
    standalone = _REGISTRY.standalone(domain)
    return standalone.compile(tools, checkpointer) if standalone is not None else None


def make_deterministic_node(
    tools_factory: dict[Domain, Callable[..., Any]] | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> Any:
    """Single-domain, unambiguous request: dispatch to that domain's own
    complete, already-tested standalone subgraph (User Stories 3/4) — never
    the FanOut propose-and-hold prefix (see module docstring).

    This node manually invokes a *separate* compiled graph rather than being
    a mounted LangGraph subgraph node, so a nested interrupt does not
    propagate to the outer graph for free — it is forwarded explicitly (see
    the `interrupt(payload)` call below). Disclosed limitation of that
    forwarding: on the pass where the inner subgraph first interrupts, this
    node's own execution is abandoned by the same `interrupt()` raise before
    it can return the inner subgraph's already-completed writes (e.g.
    dining's `hold_placement` result) — so the *outer* graph's state
    snapshot at that point shows only `__interrupt__`, not yet
    `awaiting_approval`/`holds`/`proposals`. Those become visible once the
    outer graph resumes and this node's return statement actually executes.
    A caller must therefore treat `__interrupt__` as the signal that a
    decision is pending here, not `awaiting_approval` — this is a real,
    disclosed gap in this manual-dispatch approach, not an oversight; a
    mounted-subgraph-node redesign would close it but is out of scope for
    this feature."""
    tools_factory = tools_factory or DEFAULT_TOOLS_FACTORY

    async def deterministic(state: ResortFlowState) -> dict[str, Any]:
        update = await _deterministic_core(state, tools_factory, checkpointer)
        # Feature 006 convergence (T052, CRITICAL): DETERMINISTIC routes
        # straight to END (never through POLICY_POSTFLIGHT/OFFER_AGENT/
        # RESPOND), so the self-exclusion gate before "the Offer Agent runs
        # at all, on every turn" (Constitution Principle IV) was unmet for
        # this path — ~60% of traffic. Calling the same functions the
        # SUPERVISOR path's nodes call, directly, closes that gap without
        # mounting RESPOND as a graph node here. Applied to every return path
        # from `_deterministic_core` (including "couldn't tell what you'd
        # like to book"), not only the happy path — the gate must apply
        # regardless of topic, per FR-001.
        merged_state = {**state, **update}
        postflight_update = await _apply_policy_postflight(merged_state)
        offer_update = await _apply_offer_agent(merged_state, tools_factory)
        extra_messages = [*postflight_update.get("messages", []), *offer_update.get("messages", [])]
        # Feature 008 fix: the "RESPOND would re-summarize a booking this
        # function's own inner subgraph already confirmed" concern this
        # comment used to raise turned out not to hold for the success path
        # — `confirm.py`'s happy-path return never appends a message at all
        # (verified by reading it, not assumed), so nothing was left to
        # double up. That left every ordinary single-domain confirm with no
        # guest-facing confirmation text whatsoever, surfaced by the feature
        # 008 verification console echoing the guest's own input back as the
        # "reply" since `messages[-1]` was never updated.
        #
        # `update["messages"]` here is `compiled.ainvoke`'s *full* final
        # `messages` channel (LangGraph merges full state, not a per-node
        # delta) — it always contains at least the guest's own latest
        # message, so `not update.get("messages")` is never true and cannot
        # be used as the guard (a bug in this fix's first attempt, caught by
        # re-testing against the live console rather than assumed correct
        # from a passing type-check). The real signal is whether the inner
        # subgraph's own execution appended a *new* AI-authored reply after
        # that human message — `add_messages` only ever appends, so the
        # human message stays last exactly when nothing new was added this
        # turn. Compose our own only in that case; skip it when the inner
        # subgraph already has its own text (allergen_check's block message,
        # "couldn't tell what you'd like to book", "isn't available
        # standalone yet") — reproducing `_compose_respond_message`'s own
        # "RELEASE already messaged" guard for exactly the same reason.
        last_message = (update.get("messages") or [None])[-1]
        if not isinstance(last_message, AIMessage):
            respond_update = await _compose_respond_message(merged_state)
            extra_messages = [*extra_messages, *respond_update.get("messages", [])]
        if extra_messages:
            update["messages"] = [*update.get("messages", []), *extra_messages]
        extra_flags = [*postflight_update.get("policy_flags", []), *offer_update.get("policy_flags", [])]
        if extra_flags:
            update["policy_flags"] = [*update.get("policy_flags", []), *extra_flags]
        return update

    return deterministic


async def _deterministic_core(
    state: ResortFlowState,
    tools_factory: dict[Domain, Callable[..., Any]],
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> dict[str, Any]:
    """The original `DETERMINISTIC` body (single-domain dispatch to a
    standalone subgraph), factored out so `make_deterministic_node` can
    apply `_apply_policy_postflight`/`_apply_offer_agent` uniformly to
    every return path — see that wrapper's docstring."""
    text = _latest_human_text(state)
    # Feature 010: read the decision INTENT_ROUTER already made, rather
    # than re-scanning the message a second time — falls back to
    # `_detect_domains` when `routing` is absent (a raw state dict built
    # without going through INTENT_ROUTER, e.g. an older direct-call test).
    routing = state.get("routing")
    if routing is not None and routing.kind in (RequestKind.LOOKUP, RequestKind.BROWSE) and routing.secondary_services:
        # FR-020's mixed lookup+book case, reached via BOOKING_LOOKUP's (feature 011: or BROWSE's, same shape)
        # own routing — the booking half's services live in
        # `secondary_services`, never `services` (the lookup half's own).
        domains = list(routing.secondary_services)
    elif routing is not None and routing.services:
        domains = list(routing.services)
    else:
        domains = _detect_domains(text)
    if not domains:
        return {"messages": [AIMessage(content="I couldn't tell what you'd like to book.")]}
    domain = domains[0]
    factory = tools_factory.get(domain)
    tools = factory(state["tenant_id"], state["guest_id"]) if factory else None
    compiled = _compile_standalone_graph(domain, tools, checkpointer) if tools is not None else None
    if compiled is None:
        return {
            "messages": [
                AIMessage(content=f"{domain.value.title()} booking isn't available standalone yet.")
            ]
        }
    return await run_in_subthread(compiled, state, domain)
