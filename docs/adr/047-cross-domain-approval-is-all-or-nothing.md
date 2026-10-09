# ADR-047 — The cross-domain approval gate is deliberately all-or-nothing

**Status**: Accepted
**Date**: 2026-09-14
**Feature**: 005-multi-agent-orchestration (decision retroactively documented; surfaced
while scoping the feature-008 verification console)

## Context

`graph/supervisor.py`'s `make_human_approval_node()` (the single, cross-domain approval
gate for a multi-domain itinerary — distinct from each domain subgraph's own
single-domain `human_approval` node, which `FanOut` never invokes on the multi-domain
path) resolves a guest's decision to exactly one of three global outcomes for the
*entire* held-proposal set: `approved -> COMMIT`, `modified -> PLANNER`, anything else
(including an explicit `declined`) `-> RELEASE`. There is no way to approve one
proposal in a multi-domain itinerary while declining another in the same turn.

This rationale has existed only as a docstring since the node was written in feature 005
(`graph/supervisor.py:1238-1244`):

> "the approve/decline/modify decision has no natural home in the frozen `ResortFlowState`
> schema (adding a fourth scratch key for it would be exactly the kind of piecemeal
> amendment ADR-023 argues against for something with zero use beyond this one routing
> decision)"

Constitution Principle requires a recorded ADR for significant design decisions; a
docstring is not that record. This ADR was prompted by scoping work for a manual
verification console (feature 008) that initially assumed per-item accept/decline was
either already supported or a simple UI-layer decision. It is neither.

**Verified before writing this ADR** (not assumed): `graph/state.py:177` —
`approval_scope: str | None`, not `list[str]`. It is built by
`make_proposal_assembly_node()` as `"; ".join(f"{p.title} ({p.domain.value})" for p in
held)` — a flat, human-readable display string for the interrupt payload — and is
actively used for exactly that purpose (rendered to the guest, cleared on decision). It
is not a vestigial or unused field, and it was never a structured per-proposal list.

**Also verified**: `specs/005-multi-agent-orchestration/spec.md`'s acceptance scenarios
describe the guest approving *"the itinerary"* as a single decision throughout (e.g. "a
guest approves a multi-domain itinerary, When commitment begins..."). Nothing in the
spec's functional requirements or acceptance scenarios calls for per-proposal granularity.
**This is therefore not a spec-implementation divergence** — the implementation matches
what feature 005 actually asked for. The gap is documentation (no ADR existed), not
behavior.

## Options Considered

**1. Keep all-or-nothing, exactly as built.** Advantage: matches feature 005's spec as
written; zero schema risk; the frozen `ResortFlowState` contract (AGENTS.md: "state is durable
and inspectable... any conversation replayable") stays untouched. Disadvantage: a guest
who likes two of three proposed bookings and dislikes the third must decline everything
and restart planning, or approve everything and cancel one afterward through a separate
flow.

**2. Add structured per-proposal decisions** — extend the interrupt payload with a
`proposal_decisions: dict[str, Literal["approve", "decline"]]` (or similarly a
`list[str]` of declined `proposal_id`s) resumed alongside `action`, and split `COMMIT`/
`RELEASE` routing per-proposal instead of per-turn. Advantage: real granular control,
the actual guest-facing behavior a booking itinerary UI would eventually want.
Disadvantage: a genuine schema amendment — `ResortFlowState`'s control-channel fields are a
frozen contract per AGENTS.md's hard rules ("never rename a graph node in a patch
release" — the same durability posture extends to control fields other in-flight
threads depend on), and `COMMIT`/`RELEASE` today both operate on the full held set in one
pass (`graph/supervisor.py`'s `_confirm_node_factory`/`make_release_node`), not per
proposal — both would need real rework, not just a payload change. This is a distinct
concern from `modified`: `modified -> PLANNER` already means "replan the whole
itinerary," which has no need for per-item data since it discards and rebuilds the plan
wholesale; per-item *approve/decline* is a different, additive capability that
`modified`'s existing path does not provide a shortcut for.

**3. Fake granularity in a client UI only** (show checkboxes, but collapse the guest's
selection into today's single `action` before resuming). Advantage: no backend change at
all. Disadvantage: dishonest — a UI that renders per-item controls but silently discards
partial selections (e.g. treats "2 of 3 approved" as an all-or-nothing `declined`) misleads
whoever is using it, which is exactly the failure mode AGENTS.md's "policy is not a
prompt" and "state is inspectable" commitments exist to prevent in spirit.

## Decision

**Adopt Option 1 for now**: the all-or-nothing gate is correct, intentional, and
consistent with what feature 005 specified. No code change follows from this ADR. Option
2 (structured per-proposal decisions) is named here as real, scoped future work — to be
picked up as its own feature/task when a consumer (e.g. a richer guest-facing UI) actually
needs it — not undertaken now. Any interim verification console (feature 008) must
present approval as the single decision it actually is (Option 1's shape), not simulate
granularity the backend cannot honor (rejecting Option 3).

## Consequences

**Made easier.** The frozen `ResortFlowState` schema stays untouched; no risk to in-flight
threads or the durability/replay guarantee AGENTS.md commits to. Anyone reading
`graph/supervisor.py` no longer has to infer this was deliberate from a single docstring
— it's now a recorded decision with its rejected alternatives.

**Made harder.** Nothing changes behaviorally. A guest-facing product surface that wants
true per-item approval still has to do the Option 2 work described above; this ADR does
not reduce that future cost, only documents it precisely enough that whoever picks it up
knows `modified` is not a shortcut for it.

Each domain subgraph's own single-domain `human_approval` node already exists and is
exercised today on the single-domain fast path (`FanOut` just never invokes it on the
multi-domain path). A future partial-approve therefore has a real mechanism to route
through — threading a per-domain decision to that existing per-domain gate — rather than
needing an entirely new approval primitive invented from scratch; Option 2's cost is
rework of `COMMIT`/`RELEASE` batching and the cross-domain interrupt payload, not
building per-domain approval from nothing.

**Foreclosed.** This ADR does not implement Option 2. It exists to prevent the same
question being re-investigated from scratch (as it was here) the next time a consumer of
the approval gate assumes per-item support exists.
