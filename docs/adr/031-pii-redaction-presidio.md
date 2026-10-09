# ADR-031 — PII redaction via Presidio, invoked at the trace-write boundary

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

This phase's exit criteria require "PII never lands in Langfuse." Today, redaction is
entirely unimplemented: `mcp/gateway/_shared/errors.py:147` has a `redact()` hook that is
an explicit no-op stub ("a future guest-identifying vendor field"), and
`graph/models.py`'s `ToolFailure.detail` carries a docstring promise ("Redacted before
persistence") that nothing currently honors. `pyproject.toml` has no PII-detection
dependency of any kind. The project's target platform runs "everything offline except
Azure OpenAI" (`AGENTS.md`), which constrains the redaction backend to something that
runs entirely locally, with no per-call network dependency.

The spec's clarification session settled the scope precisely: identifiers and free-text
health/dietary disclosures are redacted; the structured `DietaryRequirement.restriction`/
`.severity` enum values survive, since they are not identifying once identifiers are
removed and are required to investigate a blocked booking (SC-002, SC-004).

## Options Considered

**1. Regex/keyword-only redaction** (phone/email patterns, a name blocklist). Cheapest to
ship, zero new dependencies. Rejected: materially worse recall on names and free-text
health disclosures than an NER-backed approach — exactly the content this feature's exit
criterion is about, and exactly the content a regex has no reliable signal for (a name is
not a pattern).

**2. A cloud-hosted PII redaction API.** Rejected outright: violates the offline-by-
default constraint, and adds a network call to what is currently a synchronous,
in-process trace-write path — turning every span emission into a remote-call failure
mode this project has deliberately avoided elsewhere (the entire MCP layer exists to
centralize and bound external calls, not add new ones to logging).

**3. Presidio (`presidio-analyzer` + `presidio-anonymizer`) with a local spaCy NER model,
vendored into the Docker image at build time.** The phase roster names Presidio
explicitly. Runs fully offline once the spaCy model is baked into the image (no runtime
download). Standard, well-tested NER-backed entity recognition for exactly this problem
shape.

## Decision

**Option 3.** Add `presidio-analyzer`, `presidio-anonymizer`, `spacy`, and the
`en_core_web_sm` model (small model chosen over `_lg`/`_trf` specifically for image-size
and offline-footprint reasons — this is a redaction pass on operational telemetry, not a
guest-facing quality surface, so the smallest model meeting the recall bar wins) as new
`pyproject.toml` dependencies, with the spaCy model layer added to the Docker build
(`docker/`, Track F) rather than fetched at container start. One function,
`observability/redaction.py:redact_for_trace(payload: dict) -> dict`, wraps Presidio and
is called at every trace/log write boundary, and is also what finally implements
`mcp/gateway/_shared/errors.py:147`'s `redact()` stub — one implementation, not two. An
explicit field-name allowlist (`contracts/redaction-contract.md`) exempts
`DietaryRequirement.restriction`/`.severity` and `PolicyFlag.rule`/`.disposition` from the
NLP pass, keyed by field name, not by the NLP pass simply failing to match them.

## Consequences

**Made easier.** `ToolFailure.detail`'s two-feature-old docstring promise becomes true.
Every future feature that writes a trace/log record gets redaction by routing through
this one function, rather than each needing its own PII-handling logic.

**Made harder.** The Docker image grows by a spaCy model's size; CI/build steps that
weren't previously offline-verified now need to confirm the model is baked in, not
fetched — a new thing to get right in `docker/`, though a one-time cost, not a recurring
one.

**Foreclosed.** No future feature may write a trace/log record through a path that
bypasses `redact_for_trace` — this ADR establishes it as the single redaction boundary,
the same way `observability/attributes.py` is already the single source of attribute
names. Adding a new sensitive-field category later means extending the allowlist/
Presidio recognizer configuration in this one module, not adding ad hoc redaction
elsewhere.
