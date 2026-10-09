# ADR-056 — `ResortFlowState` amendment: `routing` and `turn_baseline`, additive, minor version bump

**Status**: Accepted
**Date**: 2026-09-28
**Feature**: 010-tiered-intent-routing

## Context

Feature 001 froze `ResortFlowState`'s key set; feature 005 (ADR-023) established the pattern for
amending it additively rather than editing the frozen contract in place. This feature needs two
new facts to survive across a superstep boundary that `ResortFlowState` today has no room for: what
`INTENT_ROUTER` decided this turn (needed by the routing edge itself, by `DETERMINISTIC`/
`PLANNER`, and — critically — by *next* turn's `INTENT_ROUTER` for one-turn follow-up carry-over,
FR-008a), and what booking records existed when this turn started (needed to compute "this turn's
outcome" for the reply and the console, FR-031 to FR-035; research R-12 traces today's false
"confirmed" reply directly to the absence of this fact). Both are genuinely new information, not a
derived view of an existing key — `_route_from_intent_router` today computes its decision inline,
in a conditional edge, which cannot write state and is never traced.

## Options Considered

**1. Re-derive both facts on demand** — re-run the router against the previous human message to
recover the prior decision, and diff `messages` against `confirmations`/`holds` timestamps to
recover the turn boundary. Rejected: the prior decision may have been made by the tier-2
classifier, which is not reproducible offline (a different model response, or none, on replay), and
neither `Hold` nor `Confirmation` carries a timestamp precise enough to reconstruct which superstep
produced it.

**2. Hide both facts in `AIMessage.additional_kwargs`.** No `ResortFlowState` amendment needed. Rejected:
this is state that happens to ride inside a message-history channel, not inspectable state — it
would be silently lost if messages are ever trimmed or summarized (a windowing strategy plan.md's
"Recorded deviations" already flags as deferred), and FR-029 requires the routing decision to be a
first-class, traced fact, not an incidental payload on a channel that exists for a different
purpose.

**3. Two additive `ResortFlowState` keys, `routing` and `turn_baseline`, each with exactly one writer
(`INTENT_ROUTER`) and no reducer** (the chosen shape) — the same additive pattern ADR-023 already
established for `graph_version`/`clarification_count`/`escalate_reason`.

## Decision

**Option 3.** `routing: RoutingDecision | None` and `turn_baseline: TurnBaseline | None`, both
defaulting to `None`, both written exactly once per turn by `INTENT_ROUTER` and read by everything
downstream that needs them. Last-write-wins is correct here, not a shortcut: no other node ever
writes either key in the same superstep, so the concurrent-writer problem the existing reducers
(`merge_holds`, `append_only`) solve for other channels does not arise. `TurnBaseline` additionally
carries `turn_index`, an incrementing counter not used for reporting in this feature — it is the
turn identity a future turn-stamped saga-event log (see ADR-058's Options Considered, option b)
would need, recorded now so that follow-up feature does not also need a `ResortFlowState` amendment of
its own.

`GRAPH_VERSION` moves `"5.0"` → `"5.1"` — a **minor** bump, deliberately. `_route_from_entry`'s
existing rule releases every outstanding hold and asks the guest to restart only on a *major*
mismatch; a pre-010 conversation resumed after this feature ships must keep its holds and continue
normally, which a minor bump guarantees and a major one would have broken on the very first
post-upgrade turn for every in-flight conversation. `escalate_reason`'s closed vocabulary gains one
appended value, `"modify_request"` — appended, per ADR-023's own foreclosed consequence, never
repurposing `"compensation_failed"` or any existing value.

## Consequences

**Made easier.** The routing decision becomes a traced, replayable fact (FR-029) instead of being
recomputed and discarded every turn. Carry-over (FR-008a) and per-turn reporting (FR-031) both
become simple reads of already-persisted state rather than new inference over message history.

**Made harder.** `INTENT_ROUTER` is now the sole writer of two keys with real invariants
(`RoutingDecision`'s model validators; `TurnBaseline`'s baseline-vs-current-state relationship) —
a future feature that wants either key written from a second node must revisit this ADR rather than
just adding a write.

**Foreclosed.** No future feature may repurpose `turn_index` for anything requiring exact
wall-clock ordering across process restarts — it is a per-conversation counter, not a global
sequence, and today's feature does not use it for
anything beyond preparing the follow-up log.
