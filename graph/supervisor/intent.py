"""Intent routing and the clarification loop."""

from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from opentelemetry import trace as otel_trace

from graph import nodes as N
from graph.bookings import booking_view, match_bookings
from graph.models import (
    Domain,
    RequestKind,
)
from graph.routing.classifier import RoutingClassifier, build_default_classifier
from graph.routing.router import route as route_intent
from graph.state import MAX_CLARIFICATION_COUNT, ResortFlowState
from graph.supervisor.common import _latest_human_text
from graph.supervisor.session import _compute_previous_turn, _compute_turn_baseline
from observability.telemetry import annotate_routing


def make_intent_router_node(classifier: RoutingClassifier | None = None) -> Any:
    """Feature 010 (ADR-055): the real routing decision now lives HERE —
    a node, not a conditional edge, so it can write state and be traced
    (`annotate_routing`). Runs the tiered router
    (`graph.routing.router.route`) and writes `state["routing"]` +
    `state["turn_baseline"]`; `_route_from_intent_router` becomes a pure
    lookup from `routing.kind` to a destination node."""
    classifier = classifier or build_default_classifier()

    async def intent_router(state: ResortFlowState) -> dict[str, Any]:
        text = _latest_human_text(state)
        previous = _compute_previous_turn(state)
        decision = await route_intent(text, previous=previous, classifier=classifier)

        if decision.kind == RequestKind.LOOKUP and not decision.focus_keys:
            records = match_bookings(
                booking_view(state), services=decision.services, text=text, today=date.today()  # noqa: DTZ011
            )
            decision = decision.model_copy(update={"focus_keys": [r.key for r in records]})

        span = otel_trace.get_current_span()
        if span.is_recording():
            annotate_routing(span, decision)

        update: dict[str, Any] = {"routing": decision, "turn_baseline": _compute_turn_baseline(state)}
        if decision.kind == RequestKind.BROWSE:
            # Feature 011 (FR-005, research R-10): a browse resolves the clarification rather than failing it, so
            # answering the assistant's own question with "list the options" must not consume the bounded budget.
            update["clarification_count"] = 0
        return update

    return intent_router


def _route_from_intent_router(state: ResortFlowState) -> str:
    """Feature 010 (`contracts/graph-topology-v010.md`): a pure lookup
    from `state["routing"]`, no matching logic of its own. `lookup`/
    `modify`/`out_of_scope`/`smalltalk` fall through to `CLARIFY` until
    their own destination nodes land (US2/US3/US4 each add their branch
    here) — never to a crash, since `CLARIFY` already exists and handles
    "undecided" the same way."""
    routing = state.get("routing")
    if routing is None or routing.kind is None:
        return N.CLARIFY
    if routing.kind == RequestKind.OUT_OF_SCOPE:
        return N.DECLINE
    if routing.kind == RequestKind.SMALLTALK:
        return N.SMALLTALK
    if routing.kind == RequestKind.MODIFY:
        return N.MODIFY_REQUEST
    if routing.kind == RequestKind.LOOKUP and routing.services == [Domain.GAMING] and not routing.secondary_services:
        # A gaming-only lookup ("what's my loyalty tier") is read through the gated gaming graph, never through
        # booking lookup: that graph holds `require_gaming_clear` and the jurisdiction's mandated message, and
        # there is no gaming booking to look up (Constitution IV; the adversarial suite found the old order).
        return N.DETERMINISTIC
    if routing.kind == RequestKind.LOOKUP:
        return N.BOOKING_LOOKUP
    if routing.kind == RequestKind.BROWSE:
        # Feature 011: a gaming-only browse goes to the existing gated gaming graph (require_gaming_clear +
        # mandated message live there once); every other browse is answered by the read-only BROWSE node.
        return N.DETERMINISTIC if routing.services == [Domain.GAMING] else N.BROWSE
    if routing.kind == RequestKind.FAQ and not routing.services:
        return N.KNOWLEDGE_QA
    # A gaming-services-only book/faq/lookup routes through the existing
    # read-only gaming standalone graph, same as any other single-service
    # book request.
    if routing.services == [Domain.GAMING]:
        return N.DETERMINISTIC
    if routing.kind == RequestKind.BOOK and len(routing.services) == 1:
        return N.DETERMINISTIC
    if routing.kind == RequestKind.BOOK and len(routing.services) >= 2:
        return N.SUPERVISOR
    if routing.kind == RequestKind.FAQ and routing.services:
        return N.KNOWLEDGE_QA
    return N.CLARIFY


_CLARIFY_QUESTION = (
    "What would you like to arrange — dining, a show or event, spa and wellness, or a room "
    "(stay)? And for which date(s)?"
)
"""Names the options instead of the old generic "Which resort experience would
you like, and for when?" (found live, demo report: a guest who answered that
with "list the resort experiences" had nothing to pick from)."""


def make_clarify_node() -> Any:
    """ADR-024: bounded clarification cycle. `interrupt()` asks a single
    follow-up question; resume value is appended as a synthetic human
    message so `IntentRouter` re-classifies against the combined text."""

    async def clarify(state: ResortFlowState) -> dict[str, Any]:
        count = state.get("clarification_count", 0)
        if count >= MAX_CLARIFICATION_COUNT:
            return {
                "escalate": True,
                "escalate_reason": "clarification_bound",
                "messages": [
                    AIMessage(
                        content="I still can't tell what you'd like to book — connecting you with a team member."
                    )
                ],
            }
        answer = interrupt(
            {"question": _CLARIFY_QUESTION}
        )
        answer_text = answer if isinstance(answer, str) else str(answer)
        from langchain_core.messages import HumanMessage

        return {
            "clarification_count": count + 1,
            "messages": [HumanMessage(content=answer_text)],
        }

    return clarify


def _route_from_clarify(state: ResortFlowState) -> str:
    return N.HUMAN_HANDOFF if state.get("escalate") else N.INTENT_ROUTER
