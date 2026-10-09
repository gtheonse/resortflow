# ADR-041 — Shared, cloud-agnostic telemetry module

**Status**: Accepted
**Date**: 2026-09-13
**Feature**: 007-eval-observability-gate

## Context

The constitution's Development Standards already require that "every external
call and every graph node MUST emit an OpenTelemetry span carrying both
`gen_ai.*` and `langfuse.*` attribute namespaces via the telemetry module" and
that "attribute names MUST NOT be defined outside that module." Research found
this requirement is currently unmet: no `telemetry.py` module exists anywhere.
Instead, two independent, ad-hoc tracers exist — `agents/dining/tracing.py`
(Langfuse-specific, dining domain only) and `mcp/gateway/_shared/tracing.py`
(bare OTel, gateway only) — and every other domain (entertainment, stay,
wellness, gaming) and `graph/supervisor.py` emit no spans at all. Both existing
tracers' own docstrings identify building the shared module as "Feature 007's
job." Separately, this feature's own cloud-agnostic requirement (FR-013–016)
means whatever module is built must not hardcode a vendor identifier — the
exact `gen_ai.system = "az.ai.openai"` pattern ADR-039's cloud portability
review already flagged as a Tier-1 risk, currently only present in
documentation, not code.

## Options Considered

**1. Leave the two existing tracers as-is, add a third ad-hoc tracer for the
remaining domains.** Advantage: least code churn to existing dining/gateway
call sites. Disadvantage: entrenches three independently-evolving attribute
sets instead of two — the exact drift the constitution's "no attribute names
outside the module" rule exists to prevent — and does nothing to close the
"every node" gap for `graph/supervisor.py`.

**2. One shared `observability/telemetry.py` module** exposing
`start_agent_span()`/`set_payload()`, with the two existing tracers becoming
thin wrappers over it, and every currently-silent domain/node gaining its first
span emission through the same module. Exporter target selected by a standard
OTel environment variable, no vendor SDK imported by the module itself.
Advantage: single point of attribute-namespace truth, closes the existing
constitutional compliance gap, cloud-agnostic by construction. Disadvantage:
touches five domains' node wrappers plus the graph supervisor — the widest
file-touch surface of any decision in this plan.

**3. Adopt Foundry/Application-Insights-specific instrumentation directly**,
skipping the dual-namespace/Langfuse-compatible design, since production is
Azure-targeted. Advantage: less abstraction. Disadvantage: directly
contradicts this feature's own FR-013–016 cloud-agnostic requirement and
ADR-039's standing multi-cloud rule; also loses local, zero-cost Langfuse
development entirely (the two-environment strategy `docs/ResortFlow-Observability-
Tracing-Deep-Dive.md` already commits to).

## Decision

**Adopt Option 2.** `observability/telemetry.py` becomes the sole place any
span attribute is set, with `gen_ai.system` and any other vendor-flavored value
read from configuration rather than hardcoded, and the exporter target
selected via the standard `OTEL_EXPORTER_OTLP_ENDPOINT` variable so it can point
at Langfuse locally or any other OTLP-speaking backend elsewhere without a code
change.

## Consequences

**Made easier.** Every future domain automatically gets both attribute
namespaces correctly by calling one function; no future feature can
accidentally set a raw attribute outside the module because there is exactly
one place that imports the OTel API for span attributes. Backend swaps
(Langfuse → any other OTLP consumer) are a config change, not a code change.

**Made harder.** This feature must touch every domain's node wrapper plus
`graph/supervisor.py` — a wider file-touch surface than a domain-scoped feature
would normally have, and a regression here would affect tracing across the
entire platform at once rather than one domain.

**Foreclosed.** Large-payload externalization to blob storage (mentioned in the
Deep Dive doc's fuller design) is out of scope for this pass — payloads are
truncated with a marker attribute instead. That gap is recorded, not silently
dropped (see plan.md Complexity Tracking).
