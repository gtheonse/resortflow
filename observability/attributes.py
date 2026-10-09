"""T008 — the frozen telemetry attribute vocabulary.

FR-022. Freezes attribute *names* only. Emission (the tracer, the exporter,
the dual-backend callback handlers) is feature 007's responsibility. This
module is the single place any attribute name may be defined, per the
constitution's OBSERVABILITY IS NOT OPTIONAL standard — a later feature
asserts that no attribute-name string literal appears outside this module.

Transcribed verbatim from
``specs/001-contracts-foundation/contracts/telemetry-attributes.md``.
"""

from __future__ import annotations

from typing import Final

# --- The five identifiers -------------------------------------------------
#
# Cardinality rule: all five are span attributes only — never metric
# dimensions. `saga_id` deliberately outlives `trace_id`: a hold placed in
# turn 3, approved in turn 5, compensated in turn 6, is three traces and one
# saga.

SESSION_ID: Final[str] = "session_id"
TRACE_ID: Final[str] = "trace_id"
SPAN_ID: Final[str] = "span_id"
SAGA_ID: Final[str] = "saga_id"
IDEMPOTENCY_KEY: Final[str] = "idempotency_key"

UNBOUNDED_IDENTIFIERS: Final[frozenset[str]] = frozenset(
    {SESSION_ID, SAGA_ID}
)
"""Span attributes only, never metric dimensions (`guest_id` is state-only,
not a telemetry identifier defined here)."""

TENANT_ID: Final[str] = "tenant_id"
OUTCOME: Final[str] = "outcome"

BOUNDED_METRIC_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {TENANT_ID, "node", "domain", "model", OUTCOME, "kind", "tier", "tier2_outcome"}
)
"""Safe as metric dimensions — bounded cardinality.

`node` is bounded by ``graph.nodes.ALL_NODES``: every workflow-step identity
emitted in production must resolve to a string constant declared in that
registry.
"""

# --- Namespaces -------------------------------------------------------------
#
# Every span representing a model or tool operation carries attributes in
# all three namespaces simultaneously, so instrumentation is portable
# between the local Langfuse backend and the production Foundry/Azure
# Monitor backend.

GEN_AI_NAMESPACE: Final[str] = "gen_ai"
"""Foundry / GenAI semantic conventions, e.g. `gen_ai.operation.name`,
`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`."""

LANGFUSE_NAMESPACE: Final[str] = "langfuse"
"""Langfuse's own namespace, e.g. `langfuse.observation.type`,
`langfuse.trace.metadata.node`, `langfuse.session.id`."""

RESORTFLOW_NAMESPACE: Final[str] = "resortflow"
"""Project-specific, queryable in both backends, e.g. `resortflow.node`,
`resortflow.domain`, `resortflow.tenant_id`, `resortflow.saga_id`, `resortflow.graph_version`."""

ALL_NAMESPACES: Final[frozenset[str]] = frozenset(
    {GEN_AI_NAMESPACE, LANGFUSE_NAMESPACE, RESORTFLOW_NAMESPACE}
)

# --- Prompt lineage (feature 009, ADR-052 D5 option ii) ------------------------
#
# Amendment to the frozen vocabulary, approved for feature 009 (blocker B-1):
# exactly these five `resortflow.prompt.*` attributes, emitted by
# `observability.telemetry.start_generation_span`. No `langfuse.observation.prompt.*`
# attribute is defined or emitted -- lineage is `resortflow.*` only and this project makes
# no claim that the Langfuse UI links a generation to its prompt version.
# See specs/009-prompt-management/contracts/telemetry-prompt-attributes.md.
#
# `PROMPT_SHA256` and `PROMPT_VERSION` are span attributes only, NEVER metric
# dimensions (unbounded over time), so they are deliberately not in
# `BOUNDED_METRIC_DIMENSIONS`.

PROMPT_NAME: Final[str] = f"{RESORTFLOW_NAMESPACE}.prompt.name"
PROMPT_VERSION: Final[str] = f"{RESORTFLOW_NAMESPACE}.prompt.version"
PROMPT_SHA256: Final[str] = f"{RESORTFLOW_NAMESPACE}.prompt.sha256"
PROMPT_SOURCE: Final[str] = f"{RESORTFLOW_NAMESPACE}.prompt.source"
PROMPT_REJECTED_REASON: Final[str] = f"{RESORTFLOW_NAMESPACE}.prompt.rejected_reason"

PROMPT_SOURCE_VALUES: Final[frozenset[str]] = frozenset(
    {"git", "langfuse", "git-fallback", "git-fallback-unapproved", "git-fallback-revoked"}
)
"""The five values `resortflow.prompt.source` may take (bounded)."""

# --- Routing decision (feature 010, ADR-055, FR-029) ------------------------
#
# Amendment to the frozen vocabulary: seven `resortflow.routing.*` attributes,
# emitted by `observability.telemetry.annotate_routing` on the `intent_router`
# span. `kind`, `tier` and `tier2_outcome` are bounded enumerations and may be
# metric dimensions; `services` is a comma-joined, unbounded-in-combination
# string and is NOT a metric dimension. No attribute here ever carries guest
# message text (FR-029). See
# specs/001-contracts-foundation/contracts/telemetry-attributes.md's
# "Amendment — Feature 010".

ROUTING_KIND: Final[str] = f"{RESORTFLOW_NAMESPACE}.routing.kind"
ROUTING_SERVICES: Final[str] = f"{RESORTFLOW_NAMESPACE}.routing.services"
ROUTING_TIER: Final[str] = f"{RESORTFLOW_NAMESPACE}.routing.tier"
ROUTING_CONFIDENCE: Final[str] = f"{RESORTFLOW_NAMESPACE}.routing.confidence"
ROUTING_AMBIGUOUS: Final[str] = f"{RESORTFLOW_NAMESPACE}.routing.ambiguous"
ROUTING_CARRIED_OVER: Final[str] = f"{RESORTFLOW_NAMESPACE}.routing.carried_over"
ROUTING_TIER2_OUTCOME: Final[str] = f"{RESORTFLOW_NAMESPACE}.routing.tier2_outcome"
