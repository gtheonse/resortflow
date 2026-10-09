# ADR-011 — Five domain MCP servers as one multi-mounted process

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 003-mcp-tool-layer

## Context

The spec requires "one MCP server per domain" (five: dining, entertainment, stay, wellness,
gaming) and a runnable exit proof (an inspector enumerating every tool against a live
server). `docker-compose.yml` already reserves exactly one service and one port for this —
`mcp-gateway`, `:9100`, `build.context: ./mcp` — a skeleton feature 001 froze before this
feature's tool count or domain split was decided. `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`'s
topology table lists the same single line: `mcp-gateway :9100 — MCP servers, one per domain`.

## Options Considered

**1. Five separate containers, one per domain.** Genuine process isolation — one domain
crashing can't take another down — and independent scaling per domain if load ever diverges.
Requires editing the frozen `docker-compose.yml` topology (five new service blocks, five new
ports or a reverse proxy) and the enterprise plan's stated port allocation, none of which
currently exist.

**2. One `FastMCP` instance, tools distinguished only by a `domain` argument or a
`resortflow.<domain>.` name prefix.** No process or port changes needed at all. Rejected outright —
an inspector would see one server with ~31 tools, not five servers with ≤8 each, which makes
"one MCP server per domain" false at the protocol level, not just a deployment convenience,
and defeats the spec's per-domain enumeration success criteria (SC-001/002).

**3. Five independent `FastMCP` instances — one per domain, five real tool registries — mounted
as ASGI sub-applications inside one Starlette parent app, served by one uvicorn process on the
already-reserved port.** Satisfies "one server per domain" at the protocol level (an inspector
connecting to each mount path sees a genuinely distinct server) without touching the frozen
compose topology at all.

## Decision

**Option 3.** One process, one container, one port — matching the frozen `docker-compose.yml`
and enterprise-plan topology exactly — hosting five independently-registered `FastMCP`
instances mounted at five distinct paths behind one shared authentication and tracing
middleware chain.

## Consequences

**Made easier.** Zero changes to `docker-compose.yml` or the enterprise plan's port
allocation. The per-upstream circuit breaker (FR-024/025/031) needs no cross-process state
synchronization — five in-memory breaker instances in one process are trivially correct. One
middleware chain is one place to prove tenant-crossing is impossible by construction
(SC-003), rather than five places that could individually drift.

**Made harder.** A crash or resource exhaustion in one domain's tool handler (unlikely,
since these are thin adapters, but not impossible) can affect the whole process rather than
being contained to one domain — genuine process isolation is given up.

**Foreclosed.** Independently scaling one domain's traffic without touching the others is no
longer possible without revisiting this decision. If a future domain's load profile ever
genuinely diverges from the other four, splitting it out is a deployment change, not a
protocol change — the five `FastMCP` instances already exist as separate objects, so
extracting one into its own container is mechanical, not a rewrite.
