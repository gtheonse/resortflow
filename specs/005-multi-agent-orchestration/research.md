# Phase 0 Research: Multi-Agent Orchestration

No item in the Technical Context below carries `NEEDS CLARIFICATION` — this feature's
language, framework, storage, and testing stack are fixed project-wide (AGENTS.md,
already implemented across features 001-004), so this phase's research is about
resolving open *design* questions the codebase and docs left explicitly unresolved for
"whenever feature 005 lands," not about technology selection.

The five decisions with the largest blast radius each produced their own ADR (required by
this project's plan-template override whenever a decision constrains a later feature,
could reasonably have gone another way, or would be breaking to reverse). They are
summarized here and detailed in full at `docs/adr/`:

## Decision: OFFER_AGENT stays reserved-but-uncompiled; HUMAN_HANDOFF becomes real

**Decision**: `HUMAN_HANDOFF` is compiled as a real `interrupt()` node this feature.
`OFFER_AGENT`'s name stays in the frozen registry, untouched, but is not mounted in this
feature's graph.

**Rationale**: Human Handoff is required by this feature's own exit criteria (bounded
replan → escalation). Offer Agent's substance depends on a Policy Engine self-exclusion
gate that Phase 6 owns; compiling it now would either skip that gate or ship an empty
node.

**Alternatives considered**: compiling both (forces an unguarded Offer Agent live);
folding both into adjacent nodes (loses Human Handoff as a distinct, testable state).

**Full detail**: `docs/adr/020-offer-agent-human-handoff-topology.md`.

## Decision: All five domains adopt real MCP wire-transport, dining included

**Decision**: A shared `graph/mcp_client.py` implements the real client side of the MCP
wire transport, and dining's existing in-process `McpDiningTools` is retrofitted onto it
alongside the four new domains' tool clients.

**Rationale**: the Phase 4 handoff flagged this as needing proof "before feature
005 assumes it works." This feature is the first point with a real process boundary to
prove it against, and proving it on dining (the domain with existing eval coverage) is
more valuable than proving it only on the four untested new domains.

**Alternatives considered**: keep everyone in-process (defers the proof indefinitely);
wire only the new domains (leaves two coexisting client patterns and proves the boundary
on the least-tested code).

**Full detail**: `docs/adr/021-mcp-wire-transport-adoption.md`.

## Decision: Hold-ledger reaper ships as a standalone scheduled job

**Decision**: `infra/jobs/hold_ledger_reaper.py`, run on a 60-second cadence independent
of the gateway/supervisor process lifecycle; `HoldLedger` gains an additive
`mark_orphan_released` method.

**Rationale**: ADR-019 designed the reaper conceptually but explicitly left it unbuilt at
one-domain risk levels. Five domains committing concurrently multiplies the orphan-hold
crash surface enough that it must close now, and a standalone job avoids
multiple-replica race conditions an in-process background task would need extra locking
to avoid.

**Alternatives considered**: in-process background task (races itself under multiple
replicas); pushing the logic into Postgres via `pg_cron` (new extension dependency, harder
to unit-test than a plain script).

**Full detail**: `docs/adr/022-hold-ledger-reaper-execution-model.md`.

## Decision: `ResortFlowState` gains three additive keys

**Decision**: `graph_version: str`, `clarification_count: int` (default `0`),
`escalate_reason: str | None` (default `None`, closed set
`{"replan_bound", "clarification_bound", "compensation_failed"}`).

**Rationale**: `ResortFlow-HITL-Durability-State.md` §8's versioning mitigations assume a
`graph_version` key that doesn't exist yet; this feature is the first with concurrent
multi-domain interrupts long-lived enough to make mid-flight redeploy a real risk. The new
clarification loop needs its own bounded counter, distinct from `replan_count`. A typed
`escalate_reason` replaces "reconstruct why from the saga log by hand" for whoever
handles `HUMAN_HANDOFF`.

**Alternatives considered**: adding nothing (defers a versioning gap into the exact
feature that makes it live); adding only `clarification_count` (defers the other two past
the point they become necessary).

**Full detail**: `docs/adr/023-resortflowstate-amendment-feature-005.md`.

## Decision: A dedicated `CLARIFY` node owns the guest clarification turn

**Decision**: New frozen node `CLARIFY`, `IntentRouter --> Clarify: ambiguous`,
`Clarify --> IntentRouter: guest answered`, `interrupt()`-based, `sync` durability,
bounded by `MAX_CLARIFICATION_COUNT`, escalating to `HUMAN_HANDOFF` at the bound.

**Rationale**: Closes the gap the Phase 4 handoff named explicitly ("a real
clarification turn is a feature 005 concern"). Keeping it as its own node (rather than
looping inside `IntentRouter`, or folding it into `Planner`) keeps `IntentRouter` a pure,
fast classifier and ensures a request that resolves to simple/deterministic after
clarification never enters the Supervisor subgraph at all — honoring Principle I
(ambiguity-based routing) rather than working around it.

**Alternatives considered**: looping inside `IntentRouter` (conflates classification and
conversation); folding into `Planner` (routes pre-clarification requests into the agentic
graph too early, risking the sub-1.5s latency-budget rule).

**Full detail**: `docs/adr/024-clarification-turn-topology.md`.

## Decision: Travel-time conflict data uses a static per-tenant matrix

**Decision**: The deterministic conflict checker's travel-time lookup keys off the
`location_zone` field already present on `graph.models.Proposal` ("Travel-time input"
per its own docstring), against a small static per-tenant zone-to-zone minutes table
(`graph/travel_time.py`), not a live distance/mapping service.

**Rationale**: Both tenants are single, fixed physical properties with a small,
enumerable venue set — a live external distance API would add cost, latency, and a sixth
simulated vendor for a value that never changes absent a physical renovation. This
crosses the ADR bar because it introduces a data shape (the matrix) future domains and
venues must populate, unlike the other "smaller decisions" below.

**Alternatives considered**: a live distance/mapping API (unjustified cost and a new
external dependency for a bounded, single-property venue set); geo-coordinate haversine
calculation (more general than needed, and a poor proxy for indoor walking time without
a fudge factor that would undermine the conflict checker's determinism).

**Full detail**: `docs/adr/025-static-travel-time-matrix.md`.

## Smaller decisions (no ADR — routine application of existing patterns or non-binding choices)

### Decision: Sequential commit ordering follows the existing worked example

**Decision**: Commit order ranks by scarcity/irreversibility using the ordering already
worked in `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §6's sequence diagram: entertainment
(fixed external inventory, hardest to re-hold) before dining (moderate, resort-controlled)
before stay (most flexible, resort-owned inventory); wellness ranks with dining
(resort-controlled, moderate scarcity) pending real-world tuning once entertainment and
wellness both have production booking-pattern data.

**Rationale**: Constitution Principle III already mandates "ordered by scarcity and
irreversibility, not parallel" — this is applying that existing rule to the concrete
five-domain set, not a new architectural decision requiring its own ADR.

**Alternatives considered**: a purely configurable per-tenant ordering (deferred —
no evidence yet that Nevada/Massachusetts differ on commit-risk ordering, unlike policy
messaging, which does).

### Decision: Idempotency key generation generalizes the existing formula, unchanged

**Decision**: Every new domain's mutating tools reuse the existing formula and helper
shape already proven in `agents/dining/tools.py`'s `idempotency_key(session_id,
proposal_id, attempt)`, one instance per domain package (`agents/entertainment/tools.py`,
etc.), not a shared cross-domain function — the formula is domain-agnostic but each
domain's proposal/hold identifiers are domain-local.

**Rationale**: This is a frozen convention (`mcp-tool-contract.md`, ADR-006) being applied
to four more domains, not a new decision.

### Decision: Entertainment's MCP server follows the `stay`/`server.py` pattern exactly

**Decision**: `mcp/gateway/entertainment/server.py` (currently absent — only `models.py`
exists) is built as a `FastMCP` app mirroring `mcp/gateway/stay/server.py`'s shape:
`@app.tool()` + `@register_tool(domain="entertainment", ...)` functions per verb
(`search_events`, `hold_seats`, `purchase_tickets`, `get_order_status`,
`cancel_order`/`request_refund`), using the Pydantic response models already built in
`entertainment/models.py` (`Event`, `SearchEventsResponse`, `Section`,
`InventoryResponse`, `SeatHold`, `PurchaseResponse`, `Order`, `OrderStatus`,
`RefundResponse`), mounted into `mcp/gateway/main.py`'s app list alongside the existing
four.

**Rationale**: Routine application of an already-recorded pattern (ADR-011's
one-process-five-mounts decision); no new architectural decision.

### Decision: Eval suite expansion is a Track E coordination point, not this plan's decision

**Decision**: This plan documents how the existing `agents.dining.eval.runner` pattern
(`run_case`/`run_all`/`write_results`) generalizes to entertainment/stay/wellness and to
multi-domain supervisor scenarios (see `quickstart.md`), but does not decide the final
shape of a top-level `eval/` harness — `AGENTS.md`'s track table assigns `eval/` to Track
E, and the enterprise plan doc's parallel-execution model runs Track E's eval expansion
alongside, not subordinate to, this (Track C) phase.

**Rationale**: Respecting file-disjoint track ownership; a Track C plan overreaching into
Track E's directory is exactly the kind of cross-cutting change the working agreement
says should go through a single integration commit, not be decided unilaterally here.
