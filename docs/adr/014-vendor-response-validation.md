# ADR-014 — Vendor response validation via hand-authored Pydantic models

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 003-mcp-tool-layer

## Context

FR-034/035/036 require every vendor response on an otherwise-successful status be either a
validated conformant body or a classified failure — never a partial pass-through. Something
has to actually perform that parse-then-validate step for all ~31 tools' vendor calls, and the
mechanism chosen constrains how every future tool in this feature (and any feature that adds a
sixth vendor later) is written.

## Options Considered

**1. Runtime validation directly against the frozen OpenAPI YAML** (e.g. `openapi-core`).
Cannot drift from the contract by construction — the same file the sim was built against is
the same file validating the sim's responses. But the result of validating against a raw JSON
Schema is a validated dict, not a typed object a tool can return over MCP directly; every tool
would still convert that dict into something typed before returning it, so the "avoid
hand-authoring" advantage shrinks once that conversion step is counted.

**2. Hand-authored Pydantic v2 response models, one per vendor endpoint, mirroring each frozen
OpenAPI response schema.** Requires writing and maintaining ~31 models by hand, with the
attendant risk of a model quietly drifting from its contract if nobody notices. In exchange:
`model.model_validate(body)` is the classification decision itself (`ValidationError` →
`TERMINAL`) and the returned model is exactly what a tool hands back over MCP — one step, not
two — and the models double as readable documentation of what each tool actually returns.

## Decision

**Option 2.** Every vendor call has a hand-authored Pydantic response model. A JSON decode
failure classifies `RETRYABLE` (FR-034); a decode success that fails `model_validate` classifies
`TERMINAL` (FR-035); a validated model is the only thing a tool is ever allowed to return
(FR-036 — there is no code path that forwards a field without passing through the model).

## Consequences

**Made easier.** No new dependency (`pydantic` is already in the project). Composite tools
(FR-004) become straightforward to write correctly, since combining two validated models into
one guest-intent-shaped output is ordinary Python, not JSON Schema composition. FR-037's
effect-status logic has exactly one hook point: a `ValidationError` on a *mutating* call's
response.

**Made harder.** ~31 models need writing and keeping in sync with the frozen OpenAPI files by
hand — a schema field renamed upstream (which shouldn't happen, since those contracts are
frozen, but could happen via a legitimate future amendment) requires someone to notice and
update the corresponding model; nothing catches drift automatically the way schema-driven
validation would.

**Foreclosed.** Nothing structural. Swapping to schema-driven validation later is possible
without changing any tool's public MCP-facing schema, since the models' *output* shape is
what's frozen for callers, not the validation mechanism producing it.
