# ADR-026 — Explicit cancellation reuses the `HumanApproval` decline path

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 005-multi-agent-orchestration

## Context

spec.md's FR-022 (added via `/speckit-clarify`) requires that when a guest
explicitly cancels an in-progress itinerary after holds have been placed but
before commitment, the system proactively releases every outstanding hold
immediately, rather than leaving them to expire on their own. This needs a
home in the graph topology.

The topology only pauses for guest input at two points: `Clarify` and
`HumanApproval`. Everything between hold placement (`FanOut`) and
`HumanApproval` runs as one uninterrupted turn under `sync` durability — there
is no point in that stretch where a guest's message could arrive and be
processed as a mid-flight cancellation. The only reachable point for "guest
explicitly cancels after holds exist, before commit" is therefore the
existing `HumanApproval` interrupt, which already has a `declined` edge to
`Release` (`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §4's original
`stateDiagram-v2`, unchanged by this feature). The question this ADR answers:
is an explicit cancellation the same event as a decline, or does it need its
own signal?

## Options Considered

**1. Fold cancellation into `declined`.** No new edge, no new state field.
`declined` already means "guest does not want this plan committed, release
everything" — which is exactly FR-022's scenario. Cheapest, and consistent
with not building a distinction nothing yet consumes.

**2. A distinct `HumanApproval --> Release: cancelled` edge.** Preserves a
future ability to route declined-vs-cancelled differently (e.g., a declined
plan might one day offer a cheaper alternative; an explicit cancellation
never would). Costs a second topology edge, both targeting the identical
`Release` node, for zero behavioral difference today — the frozen node
registry gives edges no independent identity to hang different behavior off
of, so the two edges would produce identical code paths regardless.

**3. A lightweight `release_reason` field**, mirroring ADR-023's
`escalate_reason` pattern (`"declined"` | `"guest_cancelled"`). Cheap
differentiation without a new topology edge, but is exactly the kind of
piecemeal, one-key-at-a-time `ResortFlowState` amendment ADR-023 itself argued
against bundling separately — and there is no current consumer of the
distinction to justify adding it now.

## Decision

**Option 1.** Explicit cancellation and declining a proposed itinerary are
treated as the same event, routed through the existing `HumanApproval -->
Release: declined` edge, with no new signal, edge, or state field. If a
future feature needs to distinguish "declined, open to alternatives" from
"cancelled, stop entirely," that becomes a new, deliberate decision made when
a real consumer of the distinction exists — not speculative differentiation
built ahead of need.

## Consequences

**Made easier.** `Release`'s implementation has exactly one trigger to
handle, not two, and no branching logic distinguishing intents that produce
identical observable behavior.

**Made harder.** None materially — the two guest intents already result in
the same outcome (everything released, conversation ends there), so there is
no lost capability today.

**Foreclosed.** No future feature may silently start distinguishing decline
from cancel without amending this ADR — today's saga log and telemetry carry
no field that would let such a distinction be reconstructed after the fact.
