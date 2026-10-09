# ADR-029 — Non-overridable policy blocks route around `HUMAN_APPROVAL`; `policy_flags` gains an `append_only` reducer

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

`specs/006-policy-guardrails-hitl/spec.md`'s clarification session settled that an
allergen-fidelity mismatch must never be approvable by a human reviewer or the guest —
only correcting the structured record and re-running the check can clear it (FR-006).
Reading the current code against that requirement surfaces a real, not hypothetical,
conflict: `agents/dining/edges/conditions.py`'s `human_approval` node already includes
`dietary_conflicts` in its `interrupt()` payload and, on resume, computes
`approved = bool(decision.get("approved"))` unconditionally — a human (or a
misconfigured client) *can* approve straight past a flagged allergen conflict today.
`graph/supervisor.py`'s cross-domain `make_human_approval_node` has the same
unconditional approve/decline/modify shape.

Separately, `graph/models.py`'s `PolicyFlag` and `ResortFlowState.policy_flags: list[PolicyFlag]`
have existed since the frozen state contract (feature 001) with zero writers anywhere in
the codebase — this feature is the first to populate them, and the field is currently
unannotated (no reducer), meaning LangGraph's default channel semantics replace the whole
value on every write rather than accumulating it.

## Options Considered

**For the override problem:**

**1. Add an `overridable: bool` field to the interrupt payload and branch on it inside
the existing `human_approval` node(s) before honoring `decision["approved"]`.** Smallest
diff. Rejected: the safety property then depends on a conditional inside a node whose
entire other purpose is to honor approval — one future edit to that conditional (or to
how `decision` is parsed) silently reintroduces the exact gap this ADR closes, with no
structural signal that anything changed. This is precisely the "undone by a
[code] edit" fragility Constitution Principle IV warns about for prompts, applied to
code.

**2. Route the non-overridable case to the existing `BLOCKED` node instead of
`HUMAN_APPROVAL`.** `BLOCKED` has been reserved since feature 004, is currently
unreachable (no edge routes to it), and its entire contract is "this request cannot
proceed as policy stands" — no approve path exists on it at all, so there is no
conditional to weaken later. The guarantee is structural: a block that routes to
`BLOCKED` cannot be approved because `BLOCKED` has no code path that leads to `COMMIT`.

**3. A new, purpose-built interrupt-based escalation node distinct from both `BLOCKED`
and `HUMAN_APPROVAL`.** Considered for cases needing a live human decision beyond "blocked,
try again" (e.g., something a duty manager actively adjudicates in real time). Rejected
for *this* feature's scope: spec.md's own Assumptions defer a live staff console/
adjudication UI to later work, and every block type this feature must handle (allergen
fidelity, self-exclusion, dependency-unavailable) resolves by the guest/staff correcting
data and retrying on a later turn, not by a synchronous human decision mid-interrupt —
`BLOCKED` already fits that shape exactly.

**For the reducer problem:**

**1. Leave `policy_flags` unannotated, have each writer read-modify-write the full list
itself.** Rejected: this is exactly the anti-pattern `append_only` exists to prevent, and
under feature 005's fan-out (concurrent domain subgraphs in one superstep), two
concurrent flags would race to read-modify-write the same channel, guaranteed by
LangGraph's default last-write-wins semantics to lose one.

**2. Annotate with the existing `append_only` reducer.** Already imported into
`graph/state.py`, already proven correct by `tests/test_reducers.py`'s three properties
for `confirmations`/`failures`/`saga_log`. No new reducer to write or prove correct.

## Decision

**Override problem, Option 2.** A blocking allergen-fidelity result and a self-exclusion
conflict both route to `BLOCKED`, never to `HUMAN_APPROVAL`/dining's own
`human_approval`. Concretely: `agents/dining/edges/conditions.py`'s routing gains a
check — a *blocking* dietary-fidelity result (from the new `check_dietary_fidelity`,
`contracts/policy-engine-contract.md`) routes to `BLOCKED`; `human_approval`'s own body
is otherwise unchanged and remains the path for an ordinary booking approval with no
policy block. `graph/supervisor.py`'s `make_human_approval_node` needs no code change at
all — a domain that hit a policy block is simply never added to the held-proposal set
`approval_scope` is built from (the same mechanism that already excludes a
no-availability domain from a partial itinerary, feature 005 FR-021), so it never reaches
that node's approve/decline/modify contract in the first place.

**Reducer problem, Option 2.** `policy_flags: Annotated[list[PolicyFlag], append_only]`.

## Consequences

**Made easier.** The non-overridable guarantee is provable by reading the graph's edges
(does any path from a blocking check reach `COMMIT`?) rather than by reading every
`human_approval` node's parsing logic for a conditional that might have regressed. The
reducer fix costs one line and inherits three already-tested properties for free.

**Made harder.** `BLOCKED`'s guest-facing message needs to become block-reason-aware
(today it is one fixed string) rather than fully generic — a small, contained change to
`make_blocked_node`, not a new mechanism.

**Foreclosed.** No future feature may route a non-overridable policy block through
`HUMAN_APPROVAL` (or any node with an "approved" outcome) as a shortcut — `BLOCKED` is
now the documented, single home for that class of outcome, the same way ADR-026 made the
decline path the single home for explicit guest cancellation.

## Amendment — ADR-061 (2026-10-02): the block now precedes the hold

The "Decision" above leaves a blocked dining proposal's hold "to the existing hold-ledger
reaper (ADR-022) to reclaim on TTL expiry". That is **superseded**. The standalone dining graph's
`ALLERGEN_CHECK` node now has a conditional outgoing edge (`route_after_allergen_check`): a blocking
verdict ends the flow **before** `HOLD_PLACEMENT`, so no vendor inventory is held for a guest who can
never confirm. This matches what the multi-domain fan-out leg already did, and both paths now call one
function, `evaluate_allergens`, so the policy cannot diverge again.

What did not change: the block remains non-overridable, `policy_blocked` and the `PolicyFlag` pair are
set as before, `human_approval` is still never reached, and the `policy_blocked` guard in
`route_after_hold_placement` is retained as defence in depth. Node names are unchanged; only one edge was
added, so existing checkpoints remain resumable. See ADR-061 §4 and
`agents/tests/test_allergen_path_parity.py`.
