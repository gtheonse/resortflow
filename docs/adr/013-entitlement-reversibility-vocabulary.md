# ADR-013 — Entitlement vocabulary tracks reversibility, not mutation

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 003-mcp-tool-layer

## Context

`mcp-tool-contract.md` fixes the scope *shape* (`<domain>:<action>`) but not the vocabulary of
`<action>` values. This feature has to pick one before any tool can declare a real entitlement.
The choice constrains every domain server this project ever adds — changing the granularity
later means re-declaring every existing tool's entitlement.

## Options Considered

**1. Per-tool scopes** — every one of the 31 tools gets its own distinct entitlement string
(`dining:hold_table`, `dining:release_hold`, ...). Maximum precision — a caller can be granted
exactly one tool and nothing else. Duplicates the addressability the tool registry already
provides (a caller either can or can't invoke a specific tool by name) without adding a
boundary that matters architecturally, and produces a ~31-entry grant surface to reason about
and keep in sync as tools are added.

**2. Per-domain, two-tier (`read` / `mutate`)** — ten scopes total. Matches the
`<domain>:<action>` shape and keeps the grant surface small, but collapses "place a reversible
hold" and "commit an irreversible booking" into one `mutate` grant — exactly the boundary
Principle III and ADR-006 already treat as the single most important line in the whole
platform (the human-approval interrupt sits there).

**3. Per-domain, three-tier (`read` / `hold` / `commit`)** — fifteen scopes total, still fitting
`<domain>:<action>`. Tracks the reversible/irreversible boundary directly: a caller granted
`read`+`hold` can advance a booking to the edge of commitment but cannot cross it alone.

## Decision

**Option 3.** Every tool declares exactly one of `read`, `hold`, or `commit`. A `read` tool
performs no mutation; a `hold` tool performs only reversible mutation; a `commit` tool performs
an irreversible one. The three are independent grants — holding `read` and `hold` never
implies `commit`.

## Consequences

**Made easier.** The entitlement boundary now says something architecturally true instead of
merely convenient: a domain subgraph can be safely granted everything up to and including
placing holds, and the only thing standing between it and an irreversible action is a grant
it structurally does not have — which is exactly where Principle III's human-approval
interrupt is supposed to sit. Testing this boundary is one matrix (15 scope strings × 31
tools), not 31 independent judgment calls about what each tool "should" require.

**Made harder.** A caller cannot be granted access to exactly one hold-tool and denied
another in the same domain (e.g., "may hold a room but not a table") — the vocabulary is
coarser than per-tool. This was accepted deliberately: per-tool precision duplicates
addressability the registry already gives for free, without buying a boundary that matters.

**Foreclosed.** A future entitlement need finer than domain-plus-reversibility-tier (e.g.,
per-resource-type limits within one domain) requires either a new axis alongside this one or a
revisit of this ADR — it cannot be expressed by widening the existing three-value enum without
losing the property that made this choice worth making.
