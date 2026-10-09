# ADR-005 — MCP as the sole integration surface

**Status**: Accepted
**Date**: 2026-09-09
**Feature**: 001-contracts-foundation

## Context

ResortFlow integrates five simulated vendor systems (dining, ticketing, PMS, casino, amenities)
across five domain subgraphs (ADR-001), each potentially calling multiple tools per turn.
The five vendor specs are deliberately inconsistent with each other — different auth
schemes, different pagination, different date formats, and three distinct error envelope
shapes (data-model.md's error-taxonomy section documents this explicitly: dining's
`{error:{code,...}}`, ticketing's `{fault:{type,...}}`, and a flat/legacy shape shared by
amenities, pms and casino). This feature has to freeze the tool contract standard
(`docs/contracts/mcp-tool-contract.md`, FR-015/FR-016) before Track B builds anything
against it, and that contract standard is this ADR's direct consequence.

## Options Considered

**1. Each domain subgraph holds its own vendor SDK / HTTP client directly.** Fastest to
build for a single domain — no intermediary layer, no protocol to learn. At N domains and M
vendors this is an N×M integration surface: every domain re-implements auth, retry, error
normalization and tenant-scoping for every vendor it touches, and there is no single place
to audit "which agent can call which vendor with what scope."

**2. A shared internal SDK/library that every domain subgraph imports.** Collapses the N×M
problem into N call sites against one shared library — better than option 1, but the
library still runs *inside* the agent process. There is no structural barrier stopping an
agent from holding raw vendor credentials or bypassing the library's scope checks, and
per-tool audit logging depends on every call site remembering to instrument itself.

**3. Model Context Protocol (MCP) servers, one per domain, as the sole integration
surface.** Every tool call crosses a governed boundary: typed schemas, per-tool scopes,
tenant claim injected server-side (never model-supplied, FR-016), a uniform failure
envelope mapped onto this feature's four-way `ErrorClass` taxonomy (FR-015) regardless of
which of the three vendor error shapes originated it.

## Decision

**MCP, one server per domain, as the only way any agent reaches a vendor.** No agent holds
a vendor SDK — this is stated as a hard rule in `AGENTS.md` and as constitution Principle
II. The N×M integration problem collapses to one governed surface: typed contracts, uniform
audit, and — concretely, for this feature — a single normalization point for three
different vendor error shapes into one `ErrorClass` taxonomy that every domain subgraph and
every downstream consumer can rely on without knowing which vendor produced the failure.

## Consequences

**Made easier.** A tenant-crossing call becomes impossible *by construction* rather than by
convention — the tenant claim is injected server-side by MCP, never accepted from caller
arguments (FR-016), so there is no code path where a model output could supply the wrong
`tenant_id`. Every mutating tool's idempotency key is derived consistently
(`(session_id, hold_id, attempt)`), because that derivation lives once in the contract
standard, not once per domain subgraph. Adding a sixth vendor means writing one new MCP
server against the existing contract standard, not touching five existing domain
subgraphs.

**Made harder.** MCP is comparatively immature as a protocol; ResortFlow is betting on it ahead
of a fully mature ecosystem. Every tool call now has a real hop (agent → MCP server →
vendor sim) that a direct SDK call would not have, which is latency and an operational
surface (mcp-gateway, one process per domain) that has to be run and monitored.

**Foreclosed.** No domain subgraph may ever hold direct vendor credentials, even
temporarily for a quick fix — that door is closed by construction, not by policy alone.
Any future integration that doesn't fit the MCP tool-contract shape (naming, idempotency,
uniform failure envelope, scope declaration) has to either be reshaped to fit it or become
a formally justified exception via a new ADR.
