# ADR-024 — A dedicated CLARIFY node for the guest clarification turn

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 005-multi-agent-orchestration

## Context

spec.md's FR-020 (from this feature's `/speckit-clarify` session) requires the system to
ask a clarifying follow-up question when a request lacks information needed to plan,
rather than guessing. The Phase 4 review named this exact gap explicitly — "a
real clarification turn is a feature 005 concern (multi-turn orchestration)" — but left
the mechanism undecided. `graph/nodes.py`'s frozen registry has no node name for it, and
the enterprise plan's `stateDiagram-v2` has no edge for it either: `IntentRouter -->
Supervisor` already assumes intent is resolved enough to route. Some node has to own
this turn, and where it lives affects which other principles it interacts with —
particularly Principle I (ambiguity-based routing), since a request that turns out to be
simple once clarified should never have entered the agentic Supervisor graph at all.

## Options Considered

**1. Loop inside `INTENT_ROUTER` itself** — on ambiguity, it interrupts and re-invokes
itself on resume, never entering `Supervisor` until intent is resolved. No new node name
needed. Conflates two different responsibilities in one node — classification and
multi-turn conversation — and makes the node whose whole job is "decide quickly" also own
an open-ended back-and-forth.

**2. A new, dedicated `CLARIFY` node**, entered from `INTENT_ROUTER` when required fields
are missing, looping back to `INTENT_ROUTER` on the guest's answer so classification
always re-runs against the new information rather than `CLARIFY` guessing at
re-classification itself. One new frozen name, additive to the registry — not a rename.
Keeps each node single-purpose.

**3. Fold clarification into the `PLANNER`/`SUPERVISOR` boundary** — let the Planner
detect missing information and interrupt from there. Wrong boundary under Principle I: a
request that needs clarification hasn't yet been shown to require the agentic graph or
any specific domain set. Routing it into `Supervisor`/`Planner` before intent is even
resolved risks exactly the "flow with a latency budget under 1.5 seconds stays off the
multi-hop path" violation the routing principle exists to prevent, for requests that turn
out, once clarified, to be simple after all.

## Decision

**Option 2.** A new `CLARIFY` node, added additively to `graph/nodes.py`'s registry,
sitting between `IntentRouter` and its existing `Deterministic`/`Supervisor` fork:

```
IntentRouter --> Clarify: ambiguous or missing required field
Clarify --> IntentRouter: guest answered
```

The turn is `interrupt()`-based and runs under `sync` durability — extending
`ResortFlow-HITL-Durability-State.md`'s `DURABILITY_BY_INTENT` table, since an unanswered
clarification blocks any downstream commitment and is not safely `async`. It is bounded
by the new `clarification_count` / `MAX_CLARIFICATION_COUNT` (ADR-023), escalating to the
now-real `HUMAN_HANDOFF` node (ADR-020) at the bound, satisfying Constitution Principle
V's requirement that every cyclic path terminate with human escalation.

## Consequences

**Made easier.** `IntentRouter` stays a pure classifier, testable with the same
rule-based-fallback pattern Phase 4 already proved for dining's intent parser. A request
that resolves to simple/deterministic after one clarifying turn never touches the
Supervisor subgraph at all, honoring Principle I exactly rather than as an afterthought.

**Made harder.** One more frozen node name to carry forever, and one more interrupt/resume
boundary that must be proven safe under the same replay rules already established for
`HUMAN_APPROVAL` — specifically Rule 3 from `ResortFlow-HITL-Durability-State.md`: interrupts
re-trigger on replay, so any state mutation this node makes must happen before the
interrupt call, never after it.

**Foreclosed.** Clarification logic may not be duplicated inside the Planner or any
domain subgraph. A domain subgraph that receives incomplete input treats that as a tool
failure or edge case routed to Recovery, not a second clarification mechanism — `CLARIFY`
is the one place a guest is asked a follow-up question before a plan exists.
