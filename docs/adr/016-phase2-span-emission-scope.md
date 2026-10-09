# ADR-016 — Phase 2 emits real spans on a bare tracer; production wiring stays Feature 007's

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 003-mcp-tool-layer

## Context

`observability/attributes.py`'s docstring states emission — "the tracer, the exporter, the
dual-backend callback handlers" — is Feature 007's responsibility, and Phase 1's own plan
reasoned "no feature before 007 emits spans" when justifying why the simulators (as callees)
correctly emit nothing. But that same plan's next clause reads: "spans for calls *to* these
sims are emitted by the MCP adapters that make them" — assigning this exact job, for this
exact feature, to whichever feature builds the MCP adapters. This feature's own FR-027/028
also requires it directly. Something has to reconcile "no feature before 007 emits spans" with
"the MCP adapters emit spans," since both can't be read as unconditional.

## Options Considered

**1. Emit nothing; defer entirely to Feature 007.** Consistent with the broadest reading of
"no feature before 007 emits spans," but leaves FR-027/028 unimplementable and unfulfills
Phase 1's own plan's explicit assignment of this job to the MCP layer — the requirement
becomes untestable except by asserting on a tracer that doesn't exist.

**2. Build the full production pipeline now** — OTLP exporter, dual-backend (Langfuse + Azure
Monitor) callback handlers, real backend configuration. Would satisfy FR-027/028 completely,
but is scope creep into Feature 007's stated job, duplicates work that phase is meant to do
once for every feature rather than once per feature, and this feature has no way to test
against a production Azure Monitor endpoint anyway.

**3. Emit real spans, using only the frozen attribute vocabulary, through a bare tracer — an
in-memory span processor for tests, a console exporter for local dev visibility — with no
OTLP exporter and no dual-backend wiring.** Satisfies "the MCP adapters emit spans for calls
to these sims" and FR-027/028, verifiable by asserting on emitted attributes, without building
anything Feature 007 is meant to build once, project-wide.

## Decision

**Option 3.** This feature emits exactly one span per tool call (FR-027), attributes drawn
only from `observability/attributes.py` (FR-028), via `opentelemetry-api`/`-sdk` configured
with an in-memory processor for tests and a console exporter for local runs. No OTLP exporter,
no Langfuse/Azure Monitor callback handlers, no production tracer configuration — all of that
remains Feature 007's.

## Consequences

**Made easier.** FR-027/028 are actually testable now (SC-012/013 assert on emitted
attributes directly) rather than deferred to a phase three features away. Feature 007, when it
arrives, has real call sites already emitting correctly-shaped spans to wire a real exporter
onto — it configures a backend, it doesn't retrofit instrumentation into five domain servers
that were never instrumented.

**Made harder.** Until Feature 007 lands, no span from this feature is actually queryable in
Langfuse or Azure Monitor — only in test assertions and local console output. This is a
real, disclosed gap, not a silent one.

**Foreclosed.** Nothing structural. Feature 007 attaching a real exporter to an existing
`TracerProvider` is additive configuration, not a rewrite of this feature's instrumentation.
