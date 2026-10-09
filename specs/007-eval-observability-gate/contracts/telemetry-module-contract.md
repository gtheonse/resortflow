# Contract: `observability/telemetry.py`

**Produced by**: this feature (ADR-041). **Consumed by**: every LangGraph node
and every MCP gateway tool call site, present and future.

Per the constitution's Development Standards ("Attribute names MUST NOT be
defined outside that module"), this is the **only** module in the repository
permitted to call `span.set_attribute(...)` directly. Every other call site goes
through the two functions below.

## Amendment (ADR-051, feature 008-phase-7-5-gap-closure)

`start_agent_span` gained a fourth, keyword-only, optional parameter,
`session_id: str | None = None`. This is additive and backward-compatible —
every existing call site that omits it keeps working unchanged — so it does
not reopen the "Compatibility" freeze below; it extends the function body,
not its meaning. When provided, it is set as the `langfuse.session.id`
attribute (the name `observability/attributes.py` already documented but
nothing previously emitted, a gap `ui/README.md` disclosed explicitly). See
`docs/adr/051-langfuse-otlp-exporter-wiring.md`.

## Public API

```python
def start_agent_span(
    name: str, *, node: str, domain: str | None = None, session_id: str | None = None
) -> Span:
    """Single entry point for span creation. Emits both gen_ai.* and langfuse.*
    namespaces, plus resortflow.node / resortflow.domain, on every call — callers never
    choose which namespace to emit into.

    `gen_ai.system` is read from an environment variable
    (e.g. RESORTFLOW_LLM_PROVIDER), never a literal — this is the concrete guard
    against the Tier-1 portability defect ADR-039 flagged as a risk
    (docs/ResortFlow-Cloud-Portability-Review.md).

    `session_id`, added by ADR-051, sets `langfuse.session.id` when present.
    Optional and keyword-only for backward compatibility with every
    pre-existing call site.
    """

def set_payload(span: Span, kind: Literal["input", "output", "context"], value: str) -> None:
    """Applies PII masking (observability.redaction.redact_for_trace) before
    setting any attribute — no call site may set input/output/context content
    without going through this function. Oversized payloads are truncated with
    a `resortflow.<kind>.truncated` marker (large-payload externalization to blob
    storage is out of scope for this phase — see Complexity Tracking in
    plan.md).
    """
```

## Exporter selection (FR-014/015)

The exporter target is resolved once, at process start, from
`OTEL_EXPORTER_OTLP_ENDPOINT` (standard OTel env var — no ResortFlow-specific
variable is introduced for this, to stay compatible with any OTLP-consuming
backend without a translation layer). No cloud vendor's SDK is imported by this
module; it is `opentelemetry-api`/`opentelemetry-sdk` plus a generic OTLP
exporter only.

## Consumers (this feature migrates all of these to call the module above)

| Call site | Before | After |
|---|---|---|
| `agents/dining/tracing.py` | Direct Langfuse span calls | Thin wrapper calling `start_agent_span`/`set_payload` |
| `mcp/gateway/_shared/tracing.py` | Direct bare-OTel calls | Thin wrapper calling the same two functions |
| `agents/{entertainment,stay,wellness,gaming}/` | No span emission | First span emission, via this module |
| `graph/supervisor.py` | No span emission | First span emission, via this module |

## Compatibility

Both function signatures are frozen once this feature ships — a later feature
adding a new attribute or namespace extends the function bodies, never changes
the signatures without an ADR (Contract Stability).
