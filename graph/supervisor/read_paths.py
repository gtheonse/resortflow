"""Read-only and conversational paths: knowledge QA, booking lookup, browse, decline, smalltalk, modify-request hand-off."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END

from graph import nodes as N
from graph.bookings import booking_view, build_lookup_answer, match_bookings
from graph.browse_node import BrowseContext, compose_browse_reply, effective_window
from graph.models import (
    Citation,
    Domain,
    PolicyFlag,
    RequestKind,
)
from graph.replies import (
    decline_reply,
    format_current_status_line,
    format_lookup_match_line,
    modify_reply,
    smalltalk_reply,
)
from graph.state import ResortFlowState
from graph.supervisor.common import (
    _RELEVANCE_FLOOR,
    DEFAULT_TOOLS_FACTORY,
    _latest_human_text,
)
from graph.supervisor.policy_gates import _apply_offer_agent, _apply_policy_postflight

logger = logging.getLogger(__name__)


def make_knowledge_qa_node(
    tools_factory: dict[Domain, Callable[..., Any]] | None = None,
    retriever_factory: Callable[[], Any] | None = None,
) -> Any:
    """Feature 006 convergence (ADR-038, T054): the general property-
    knowledge-document Q&A path FR-007/FR-008/FR-009's own named scenario
    depends on ("a property knowledge document... contains text designed to
    look like an instruction") — no prior feature had built any retrieval
    path fed to a guest-facing answer at all; the only one that existed
    (`agents/dining/menu.py`, for allergen keyword-matching) never reached a
    generation context. This is the first one that does.

    Deterministic, not generative (Constitution Principle I): retrieves via
    `retriever_factory()` (defaults to `rag.retriever.HybridRetriever`,
    built feature 002, never wired to a guest-facing answer until now —
    injectable so tests can supply a fake, the same convention every
    domain's own `tools`/`menu_query` parameter already uses), screens
    every returned chunk through `policy.injection.screen_retrieved_content`
    before any of it reaches this response, and composes the answer by
    concatenating the clean chunks' content verbatim — no LLM call composes
    or paraphrases it. A `suspect` chunk is silently excluded, exactly like
    `agents/dining/menu.py`'s screening; the guest's answer is built only
    from what passed.

    Reached only when `IntentRouter` found zero booking-domain keywords
    (`_route_from_intent_router`) — a request already routed to
    `DETERMINISTIC`/`SUPERVISOR` never reaches this node. Falls through to
    `CLARIFY` (`_route_from_knowledge_qa`) when retrieval finds nothing
    usable — a real FAQ miss is exactly the ambiguous case `CLARIFY` exists
    for, not a new failure mode."""
    tools_factory = tools_factory or DEFAULT_TOOLS_FACTORY

    def _default_retriever_factory() -> Any:
        from rag.retriever import HybridRetriever

        return HybridRetriever()

    retriever_factory = retriever_factory or _default_retriever_factory

    async def knowledge_qa(state: ResortFlowState) -> dict[str, Any]:
        text = _latest_human_text(state)
        try:
            retriever = retriever_factory()
            hits = retriever.retrieve_hits(state["tenant_id"], text, top_k=3)
        except Exception:
            # fallback to CLARIFY, matching agents/dining/menu.py's own
            # offline-friendly pattern (logger.warning there, mirrored here),
            # not a hard failure. Previously silently swallowed with zero
            # visibility — found live (feature 008) when a real
            # DeploymentNotFound/RateLimitError from the embedding backend
            # was indistinguishable from a genuine "nothing retrieved" case
            # from the guest-facing CLARIFY fallback alone; both looked
            # identical from the outside with no trace anywhere.
            logger.warning(
                "knowledge_qa retrieval failed for tenant_id=%s — falling back to CLARIFY",
                state.get("tenant_id"),
                exc_info=True,
            )
            return {}
        if not hits:
            return {}

        # Feature 010 (FR-021/FR-022, ADR-057): a passage below the
        # calibrated relevance floor is treated the same as "nothing
        # retrieved" — the availability short-circuit
        # (`rag.retriever.is_availability_query`) already keeps this floor
        # from having to solve THAT problem; this floor targets off-topic
        # questions instead (research R-11).
        from rag.retriever import payload_to_chunk

        passing = [(chunk_id, score, payload) for chunk_id, score, payload in hits if score >= _RELEVANCE_FLOOR]
        if not passing:
            return {}
        passing = passing[:2]  # capped at the top 2 passing passages
        chunks = [payload_to_chunk(payload) for _cid, _score, payload in passing]
        citations = [
            Citation(chunk_id=c.chunk_id, source_uri=c.source_uri, quoted_span=c.content[:200])
            for c in chunks
        ]

        from policy.injection import load_injection_corpus, screen_retrieved_content

        screening = screen_retrieved_content(chunks, corpus=load_injection_corpus())
        suspect_ids = {s.chunk_id for s in screening if s.verdict == "suspect"}
        clean_chunks = [c for c in chunks if c.chunk_id not in suspect_ids]
        update: dict[str, Any] = {}
        if suspect_ids:
            update["policy_flags"] = [
                PolicyFlag(
                    flag_id=uuid.uuid4().hex, rule="injection_detected", disposition="blocked",
                    jurisdiction=state.get("jurisdiction"), raised_at=datetime.now(UTC),
                )
                for _ in suspect_ids
            ]
        if not clean_chunks:
            return update  # every candidate was suspect — fall through to CLARIFY

        answer = " ".join(c.content for c in clean_chunks)
        update["messages"] = [AIMessage(content=answer)]
        update["retrieved"] = clean_chunks
        update["citations"] = [c for c in citations if c.chunk_id not in suspect_ids]

        # This turn is not a booking (zero confirmations) — routing it
        # through RESPOND would have RESPOND stomp the FAQ answer with
        # "Nothing was booked for this request." Applying
        # _apply_policy_postflight/_apply_offer_agent directly and routing
        # to END (see _route_from_knowledge_qa) is the same fix
        # make_deterministic_node needed (T052) for the same reason.
        merged_state = {**state, **update}
        postflight_update = await _apply_policy_postflight(merged_state)
        offer_update = await _apply_offer_agent(merged_state, tools_factory)
        extra_messages = [*postflight_update.get("messages", []), *offer_update.get("messages", [])]
        if extra_messages:
            update["messages"] = [*update["messages"], *extra_messages]
        extra_flags = [*postflight_update.get("policy_flags", []), *offer_update.get("policy_flags", [])]
        if extra_flags:
            update["policy_flags"] = [*update.get("policy_flags", []), *extra_flags]
        return update

    return knowledge_qa


def _route_from_knowledge_qa(state: ResortFlowState) -> str:
    """No new `ResortFlowState` key needed: whether this turn actually answered is
    already visible in whether the *last* message is the `AIMessage`
    `knowledge_qa` would have just appended — `add_messages` guarantees a
    just-appended message sorts last. Falling through to `CLARIFY` for "no
    answer" reuses the exact node ADR-024 built for "IntentRouter couldn't
    resolve intent," rather than inventing a second one. Success routes
    straight to `END` (not `RESPOND`) — this turn is not a booking, so
    `RESPOND`'s own "nothing was booked" summary would incorrectly stomp
    the FAQ answer that was just composed."""
    messages = state.get("messages") or []
    if messages and isinstance(messages[-1], AIMessage):
        return END
    return N.CLARIFY


def make_booking_lookup_node(tools_factory: dict[Domain, Callable[..., Any]] | None = None) -> Any:
    """Feature 010, US2 (FR-014 to FR-016): answers `lookup` from THIS
    conversation's own booking records — no language model, no retrieval.
    Ends at `END`, not `RESPOND` (this is not a booking turn), so — the
    same pattern `KNOWLEDGE_QA`/`DETERMINISTIC` already use — it applies
    `_apply_policy_postflight`/`_apply_offer_agent` itself."""
    tools_factory = tools_factory or DEFAULT_TOOLS_FACTORY

    async def booking_lookup(state: ResortFlowState) -> dict[str, Any]:
        text = _latest_human_text(state)
        routing = state.get("routing")
        services = list(routing.services) if routing is not None else []
        dining_factory = tools_factory.get(Domain.DINING)
        dining_tools = dining_factory(state["tenant_id"], state["guest_id"]) if dining_factory else None

        answer = await build_lookup_answer(
            state, services=services, text=text, today=date.today(), dining_tools=dining_tools  # noqa: DTZ011
        )

        if answer.matched:
            lines = "\n".join(
                format_lookup_match_line(r, answer.recheck.get(r.key)) for r in answer.matched
            )
            content = lines
        elif answer.all_records:
            lines = "\n".join(format_current_status_line(r) for r in answer.all_records)
            content = f"I couldn't find a booking matching that in this conversation. Here's what you have:\n{lines}"
        else:
            content = "I couldn't find a booking matching that in this conversation, and you don't have any bookings in this conversation yet."

        update: dict[str, Any] = {"messages": [AIMessage(content=content)]}

        merged_state = {**state, **update}
        postflight_update = await _apply_policy_postflight(merged_state)
        offer_update = await _apply_offer_agent(merged_state, tools_factory)
        extra_messages = [*postflight_update.get("messages", []), *offer_update.get("messages", [])]
        if extra_messages:
            update["messages"] = [*update["messages"], *extra_messages]
        extra_flags = [*postflight_update.get("policy_flags", []), *offer_update.get("policy_flags", [])]
        if extra_flags:
            update["policy_flags"] = extra_flags
        return update

    return booking_lookup


def _route_from_booking_lookup(state: ResortFlowState) -> str:
    """FR-020's mixed lookup+book case: a `secondary_kind == book` on the
    same decision continues to the booking path, using
    `secondary_services` (not `services`, which belongs to the lookup
    half)."""
    routing = state.get("routing")
    if routing is not None and routing.secondary_kind == RequestKind.BOOK:
        if len(routing.secondary_services) >= 2:
            return N.SUPERVISOR
        if len(routing.secondary_services) == 1:
            return N.DETERMINISTIC
    return END


def make_decline_node(tools_factory: dict[Domain, Callable[..., Any]] | None = None) -> Any:
    """Feature 010, US3 (FR-018, SC-002): an `out_of_scope` reply. No
    retriever dependency at all — structurally, there is nothing
    knowledge-base-shaped this node could include even by accident."""
    tools_factory = tools_factory or DEFAULT_TOOLS_FACTORY

    async def decline(state: ResortFlowState) -> dict[str, Any]:
        text = _latest_human_text(state)
        update: dict[str, Any] = {"messages": [AIMessage(content=decline_reply(text))]}
        merged_state = {**state, **update}
        postflight_update = await _apply_policy_postflight(merged_state)
        offer_update = await _apply_offer_agent(merged_state, tools_factory)
        extra_messages = [*postflight_update.get("messages", []), *offer_update.get("messages", [])]
        if extra_messages:
            update["messages"] = [*update["messages"], *extra_messages]
        extra_flags = [*postflight_update.get("policy_flags", []), *offer_update.get("policy_flags", [])]
        if extra_flags:
            update["policy_flags"] = extra_flags
        return update

    return decline


def make_smalltalk_node(tools_factory: dict[Domain, Callable[..., Any]] | None = None) -> Any:
    """Feature 010, US3 (FR-019): a brief reply. No retrieval, no
    classifier — `_route_from_intent_router` only reaches this node for a
    decision `route()` already made without calling tier 2 (FR-009 never
    invokes the classifier for `smalltalk`)."""
    tools_factory = tools_factory or DEFAULT_TOOLS_FACTORY

    async def smalltalk(state: ResortFlowState) -> dict[str, Any]:
        text = _latest_human_text(state)
        update: dict[str, Any] = {"messages": [AIMessage(content=smalltalk_reply(text))]}
        merged_state = {**state, **update}
        postflight_update = await _apply_policy_postflight(merged_state)
        offer_update = await _apply_offer_agent(merged_state, tools_factory)
        extra_messages = [*postflight_update.get("messages", []), *offer_update.get("messages", [])]
        if extra_messages:
            update["messages"] = [*update["messages"], *extra_messages]
        extra_flags = [*postflight_update.get("policy_flags", []), *offer_update.get("policy_flags", [])]
        if extra_flags:
            update["policy_flags"] = extra_flags
        return update

    return smalltalk


def make_browse_node(tools_factory: dict[Domain, Callable[..., Any]] | None = None) -> Any:
    """Feature 011 (ADR-059): answers a browse request — what is available — from the providers each service
    books through, with code-composed text (`graph/browse_node.py`). Read-only: it writes no hold, proposal,
    confirmation or ledger row. Like every END-bound node it applies the policy post-flight and the offer
    agent's self-exclusion gate, so a browse can never be a way around either."""
    tools_factory = tools_factory or DEFAULT_TOOLS_FACTORY

    async def browse(state: ResortFlowState) -> dict[str, Any]:
        routing = state.get("routing")
        if routing is None:  # defensive: only reachable via a raw state dict that skipped INTENT_ROUTER
            return {"messages": [AIMessage(content="What would you like to look at — dining, shows, spa, or a room?")]}
        window, needs_dates = effective_window(routing, booking_view(state))
        ctx = BrowseContext(
            routing=routing,
            tenant_id=state["tenant_id"],
            guest_id=state["guest_id"],
            text=_latest_human_text(state),
            records=booking_view(state),
            tools_factory=tools_factory,
            today=date.today(),  # noqa: DTZ011 - local wall-clock date, same as the rest of the supervisor
            window=window,
            needs_dates=needs_dates,
        )
        update: dict[str, Any] = {"messages": [AIMessage(content=await compose_browse_reply(ctx))]}
        merged_state = {**state, **update}
        postflight_update = await _apply_policy_postflight(merged_state)
        offer_update = await _apply_offer_agent(merged_state, tools_factory)
        extra_messages = [*postflight_update.get("messages", []), *offer_update.get("messages", [])]
        if extra_messages:
            update["messages"] = [*update["messages"], *extra_messages]
        extra_flags = [*postflight_update.get("policy_flags", []), *offer_update.get("policy_flags", [])]
        if extra_flags:
            update["policy_flags"] = extra_flags
        return update

    return browse


def _route_from_browse(state: ResortFlowState) -> str:
    """A browse+book message ("show me the restaurants, and book the steakhouse") continues into the booking path
    with `secondary_services`, exactly as `BOOKING_LOOKUP` does; otherwise the turn ends."""
    routing = state.get("routing")
    if routing is not None and routing.secondary_kind == RequestKind.BOOK:
        if len(routing.secondary_services) >= 2:
            return N.SUPERVISOR
        if len(routing.secondary_services) == 1:
            return N.DETERMINISTIC
    return END


def make_modify_request_node() -> Any:
    """Feature 010, US4 (FR-017, SC-009): acknowledges a `modify`/cancel
    request, identifies the booking where determinable, and sets
    `escalate`/`escalate_reason="modify_request"` — the routing table's
    last branch (`contracts/graph-topology-v010.md`). Routes straight to
    `HUMAN_HANDOFF`, which appends its own standard hand-off line; this
    node never starts a new booking and never mutates a hold."""

    async def modify_request(state: ResortFlowState) -> dict[str, Any]:
        text = _latest_human_text(state)
        routing = state.get("routing")
        focus_keys = set(routing.focus_keys) if routing is not None else set()
        records = booking_view(state)
        if focus_keys:
            matched = [r for r in records if r.key in focus_keys]
        else:
            services = list(routing.services) if routing is not None else []
            import datetime as _dt

            matched = match_bookings(records, services=services, text=text, today=_dt.date.today())  # noqa: DTZ011 — guest-local calendar day
        return {
            "messages": [AIMessage(content=modify_reply(matched))],
            "escalate": True,
            "escalate_reason": "modify_request",
        }

    return modify_request
