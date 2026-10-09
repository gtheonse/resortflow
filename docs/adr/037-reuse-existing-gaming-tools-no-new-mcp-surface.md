# ADR-037 — Self-exclusion enforcement and `OFFER_AGENT` reuse existing `GamingTools`; no new MCP tool or domain

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

ADR-030 decided *where* self-exclusion enforcement and `OFFER_AGENT`'s data source sit
architecturally (one shared `require_gaming_clear` function; `OFFER_AGENT` scoped to the
existing loyalty composite). A separate, narrower question follows: does either of those
call sites need any *new* MCP-layer surface — a new tool, a new gateway module, a new
sim endpoint — to be implemented? `mcp/gateway/gaming/server.py` already exposes
`check_rg_status` (real, wired to `sims/casino`'s `/casino/v1/responsible-gaming/status`)
and `get_loyalty_status` (real, already a composite including optional non-gaming-comp
offers, already respecting 403→`POLICY_BLOCKED`). `agents/gaming/tools.py`'s `GamingTools`
Protocol has exactly three read-only methods and its own docstring instructs "Do not add
a fourth method."

## Options Considered

**1. Add a new MCP tool** (e.g., a dedicated `check_self_exclusion` distinct from
`check_rg_status`, or a dedicated `list_offers` distinct from `get_loyalty_status`'s
composite), reasoning that Policy-Engine-specific consumers deserve their own,
purpose-built tool shape rather than reusing a tool designed for the guest-facing lookup
flow. Rejected: `check_rg_status`'s own docstring already states it is "policy-critical,
must pass before gaming content" — it was designed, since feature 005, to be exactly this
kind of check, not merely a guest-facing lookup that happens to be reusable. A second,
near-duplicate tool returning the same `ResponsibleGamingStatus` shape would be pure
duplication with no behavioral difference, and would violate `GamingTools`'s explicit
"do not add a fourth method" contract for no benefit.

**2. Reuse `check_rg_status` and `get_loyalty_status` exactly as they exist today, with
zero MCP-layer changes.** Both are already real (not stubs), already tenant-scoped,
already respect the gaming domain's read-only guarantee, and already return everything
`require_gaming_clear`/`OFFER_AGENT` need.

## Decision

**Option 2.** No new MCP tool, no new gateway module, no new sim endpoint, no change to
`GamingTools`'s three-method Protocol. `require_gaming_clear` (ADR-030) is a `policy/`-
layer wrapper *around* the existing `check_rg_status` call — a new function in `policy/`,
not a new tool in `mcp/`. `OFFER_AGENT`'s only vendor call is the existing
`get_loyalty_status`.

## Consequences

**Made easier.** Zero new OpenAPI contract surface (`docs/contracts/openapi/` is
untouched), zero new sim work (Track A is untouched by this feature entirely), zero new
gateway registration/entitlement-scope work (Track B's only change anywhere in this
feature is the one-line `redact()` implementation, ADR-031) — this feature's Track B/A
footprint is essentially zero, a direct consequence of Discovery A in research.md.

**Made harder.** Nothing — this is the strictly smaller-surface option in every respect.

**Foreclosed.** No future feature may add a fourth method to `GamingTools` to serve a
policy-checking need specifically — `check_rg_status` already exists for exactly that
purpose, and a future addition would need to justify why the existing tool's shape is
insufficient, not merely that a differently-named tool would feel more purpose-built.
