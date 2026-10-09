"""T007 — the frozen graph node-name registry.

FR-024. String constants only — no node behaviour is defined by this
feature; feature 005 (multi-agent-orchestration) implements every node
listed here against the exact name reserved for it.

A node name is a persistence contract, not a cosmetic label: LangGraph's
checkpoint schema keys ``checkpoint_ns`` and each row's ``task_path`` by node
identity, so a rename after checkpoints exist in production is a
persistence-format break, not a refactor (AGENTS.md hard rules;
data-model.md's "Graph Node Name Registry"). This registry is also what
bounds the ``node`` telemetry dimension declared in
``observability/attributes.py`` / ``contracts/telemetry-attributes.md``.

Transcribed verbatim from ``data-model.md``'s "Graph Node Name Registry",
itself transcribed from the ``stateDiagram-v2`` in §4 of
``docs/ResortFlow-Enterprise-Multi-Agent-Plan.md``, reconciled against that
section's agent-responsibility table.

Two names — ``OFFER_AGENT`` and ``HUMAN_HANDOFF`` — were reserved but
unresolved as of feature 004. Feature 005 (ADR-020) resolves ``HUMAN_HANDOFF``
to a compiled, first-class node (`contracts/graph-topology-v005.md`) and
leaves ``OFFER_AGENT`` reserved-but-uncompiled pending the Policy Engine
dependency it needs (Phase 6+). ``CLARIFY`` is a wholly new node this
feature adds (ADR-024) — additive, not a rename of anything reserved here.
"""

from __future__ import annotations

from typing import Final

INTENT_ROUTER: Final[str] = "intent_router"
CLARIFY: Final[str] = "clarify"
"""Feature 005 addition (ADR-024). Asks a bounded follow-up question via
`interrupt()` when `IntentRouter` cannot resolve intent; see
`contracts/graph-topology-v005.md`."""
KNOWLEDGE_QA: Final[str] = "knowledge_qa"
"""Feature 006 convergence addition (ADR-038). A zero-domain request (no
booking-action keyword matched) tries retrieval-augmented FAQ answering
before falling back to `CLARIFY` — the general property-knowledge-document
Q&A path FR-007/FR-008/FR-009's own named scenario depends on, which no
prior feature had built. Deterministic: retrieves, screens
(`policy.injection.screen_retrieved_content`), and answers by composing the
screened chunks verbatim — no LLM call (Constitution Principle I)."""
DETERMINISTIC: Final[str] = "deterministic"
SUPERVISOR: Final[str] = "supervisor"
POLICY_PREFLIGHT: Final[str] = "policy_preflight"
BLOCKED: Final[str] = "blocked"
PLANNER: Final[str] = "planner"
DINING: Final[str] = "dining"
ENTERTAINMENT: Final[str] = "entertainment"
STAY: Final[str] = "stay"
WELLNESS: Final[str] = "wellness"
GAMING: Final[str] = "gaming"
CONFLICT_CHECK: Final[str] = "conflict_check"
PROPOSAL_ASSEMBLY: Final[str] = "proposal_assembly"
HUMAN_APPROVAL: Final[str] = "human_approval"
COMMIT: Final[str] = "commit"
RELEASE: Final[str] = "release"
COMMIT_VERIFY: Final[str] = "commit_verify"
RECOVERY: Final[str] = "recovery"
POLICY_POSTFLIGHT: Final[str] = "policy_postflight"
RESPOND: Final[str] = "respond"
OFFER_AGENT: Final[str] = "offer_agent"  # reserved — see module docstring
HUMAN_HANDOFF: Final[str] = "human_handoff"  # reserved — see module docstring

BOOKING_LOOKUP: Final[str] = "booking_lookup"
"""Feature 010 addition (ADR-055). Answers a `lookup` request kind from this
conversation's own booking records — no language model, no retrieval; see
`specs/010-tiered-intent-routing/contracts/graph-topology-v010.md`."""
MODIFY_REQUEST: Final[str] = "modify_request"
"""Feature 010 addition (ADR-055). Acknowledges a `modify`/cancel request,
identifies the booking where determinable, and hands off — performing the
change is out of scope."""
DECLINE: Final[str] = "decline"
"""Feature 010 addition (ADR-055). The `out_of_scope` reply: states what the
assistant cannot do and lists the services it can help with; never includes
knowledge-base text."""
SMALLTALK: Final[str] = "smalltalk"
"""Feature 010 addition (ADR-055). A brief greeting/acknowledgement reply;
never triggers retrieval or the language-model tier."""
BROWSE: Final[str] = "browse"
"""Feature 011 addition (ADR-059). Answers a browse request (what is available) from the providers each
service books through; read-only, deterministic, never knowledge-base text."""

ALL_NODES: Final[frozenset[str]] = frozenset(
    {
        INTENT_ROUTER,
        CLARIFY,
        KNOWLEDGE_QA,
        DETERMINISTIC,
        SUPERVISOR,
        POLICY_PREFLIGHT,
        BLOCKED,
        PLANNER,
        DINING,
        ENTERTAINMENT,
        STAY,
        WELLNESS,
        GAMING,
        CONFLICT_CHECK,
        PROPOSAL_ASSEMBLY,
        HUMAN_APPROVAL,
        COMMIT,
        RELEASE,
        COMMIT_VERIFY,
        RECOVERY,
        POLICY_POSTFLIGHT,
        RESPOND,
        OFFER_AGENT,
        HUMAN_HANDOFF,
        BOOKING_LOOKUP,
        MODIFY_REQUEST,
        DECLINE,
        SMALLTALK,
        BROWSE,
    }
)
