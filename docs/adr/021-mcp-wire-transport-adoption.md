# ADR-021 — Real MCP wire-transport for every domain, dining included

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 005-multi-agent-orchestration

## Context

`agents/dining/tools.py`'s `McpDiningTools` calls the MCP gateway's Python functions
in-process, not over any real transport. `agents/dining/graph.py`'s own docstring names
the reason plainly: there was no second process to put a client/server pair between until
something actually hosted a process boundary between "agent" and "gateway." This
feature's supervisor is that something — it is the first point in the project with a real
reason for the agent side and the already-running gateway process (`mcp/gateway/main.py`,
one uvicorn app on :9100 per ADR-011) to actually talk over the wire. The Phase 4 review recorded an open question: a live MCP wire-transport
integration test "should exist before feature 005 assumes it works." Nothing today
proves the real client/server hop end-to-end, and this feature is about to build four
more domains on top of whatever pattern is chosen here.

## Options Considered

**1. Keep every domain's tool client in-process, including the four new ones — defer
wire transport indefinitely.** Cheapest short-term; every domain subgraph calls gateway
functions as plain Python calls, same as dining does today. Leaves Constitution
Principle II's "governed integration surface" unproven at the only place it actually
matters: a real network boundary, with real serialization, real auth header propagation,
and real timeouts. This is precisely the gap that had to close
before this feature assumes it works.

**2. Migrate all five domains — the four new ones and a dining retrofit — onto a real MCP
client talking to the gateway process over the wire.** Fully realizes Principle II for
every domain at once, using one shared client implementation rather than five bespoke
ones.

**3. Wire only the four new domains for real transport; leave dining's in-process
shortcut as-is for this feature, with a follow-up task to retrofit it later.** Cheaper to
ship than option 2. Leaves two coexisting client patterns in the codebase at once, and
leaves the actual open question — does a real agent-to-gateway boundary work — proven for
four domains that have no eval coverage yet and unproven for the one domain (dining) that
has 25/25 passing eval cases already exercising it, which is exactly backwards from where
proof is most valuable.

## Decision

**Option 2.** All five domains, including a dining retrofit, adopt a real MCP
client talking to the already-running gateway process. Concretely: a shared
`graph/mcp_client.py` module (cross-cutting, alongside `graph/ledger.py`, not owned by
any one domain package) implements tenant/identity header propagation and a thin async
wrapper over the wire transport; each domain's `Mcp<Domain>Tools` class becomes a thin
adapter delegating to this one shared client rather than reimplementing transport
concerns five times. Coexistence of two client patterns (option 3) is exactly the kind of
inconsistency Constitution's Contract Stability standard exists to prevent, and leaving
dining's shortcut in place would leave the feature's best-tested domain as the one domain
still not proving the real boundary.

## Consequences

**Made easier.** One real, tested proof that the governed integration surface works
end-to-end closes the Phase 4 open question directly, on the domain with
the most existing test coverage to catch regressions. A single shared client module is
the one place to add retry/backoff and circuit-breaker policy later — explicitly deferred
by `mcp-tool-contract.md` ("transport... is feature 002 concerns") and overdue.

**Made harder.** Dining's fast, zero-network eval loop (25/25 cases, `agents.dining.eval.
runner`) now depends on the gateway process being reachable, changing local dev and CI
ergonomics — either the gateway must run alongside eval runs, or a fake wire-transport
double is built for the fast path while the real path is reserved for integration runs.

**Foreclosed.** No future domain subgraph may take the in-process shortcut going forward;
this ADR closes that door for anything built after this feature.
