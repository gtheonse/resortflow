# ADR-023 — Additive `ResortFlowState` amendment: graph_version, clarification_count, escalate_reason

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 005-multi-agent-orchestration

## Context

`graph/state.py`'s `ResortFlowState` is a frozen twenty-key contract; its own docstring says
adding, removing, or renaming a key is a breaking change requiring an ADR, never a
routine edit. Three gaps surface only now that this feature activates machinery Phase 4
left dormant. First, `docs/ResortFlow-HITL-Durability-State.md` §8's graph-versioning
mitigations assume a `graph_version` key pinned at thread start and compared on resume —
no such key exists, and this feature is the first with enough concurrent, long-lived
interrupts (five domains, each holding, each potentially waiting on approval) to make a
mid-flight redeploy a real operational risk rather than a theoretical one. Second, this
feature's new clarification turn (ADR-024) is its own bounded cycle under Constitution
Principle V and needs a counter distinguishable from `replan_count`, which already exists
but means something different (planner/fan-out/conflict-check attempts, not
clarification rounds). Third, `escalate: bool` already exists but cannot say which of
several genuinely different triggers fired it — replan-bound breach, clarification-bound
breach, or a failed compensation — leaving whoever (or whatever) handles `HUMAN_HANDOFF`
(ADR-020) to reconstruct the reason from the saga log by hand.

## Options Considered

**1. Add nothing.** Overload `escalate: bool` alone and infer why from `saga_log`/
`failures` at handoff time. Zero schema change, but turns a genuinely structural fact
(why did this conversation stop) into ad-hoc log parsing wherever `HUMAN_HANDOFF` is
handled, and leaves the versioning gap HITL §8 already flagged completely unaddressed
even though this feature is exactly where it becomes load-bearing.

**2. Add all three keys now** — `graph_version`, `clarification_count`, `escalate_reason`
— as one coherent amendment, each additive with a safe default per HITL §8's own rule
("additive state changes only... new fields must have defaults").

**3. Add only `clarification_count`**, the one key this feature's own new node
(`CLARIFY`, ADR-024) directly requires, and defer `graph_version`/`escalate_reason` to
whichever future feature first needs them operationally. Minimizes this feature's own
schema surface, but defers the versioning gap past the exact feature that makes
mid-flight redeploy risk concrete — five domains interrupting concurrently is the
scenario HITL §8 was written to describe, and it starts existing the moment this feature
ships, not later.

## Decision

**Option 2.** One amendment, three additive keys, all defaulted:

- **`graph_version: str`** — set once at thread start from a small build-time constant;
  compared on resume per HITL §8 mitigation #1. A major-version mismatch on resume
  releases outstanding holds and restarts the conversation per mitigation #5. This
  feature builds the field and the comparison; the restart flow itself is exercised only
  when a future feature first ships a breaking graph change, since none has happened yet
  — that flow is described here but its test coverage is necessarily limited to the
  comparison logic, not a real version bump in production.
- **`clarification_count: int`**, default `0` — incremented each time `CLARIFY` asks a
  follow-up question, bounded by a new `MAX_CLARIFICATION_COUNT` constant co-located with
  `MAX_REPLAN_COUNT` in `graph/state.py`.
- **`escalate_reason: str | None`**, default `None` — one of a small closed set
  (`"replan_bound"`, `"clarification_bound"`, `"compensation_failed"`), set exactly once,
  at the point escalation is decided.

## Consequences

**Made easier.** `HUMAN_HANDOFF` and any future eval or analytics work get a first-class,
typed reason for escalation instead of parsing `saga_log` by hand. The versioning gap
HITL §8 flagged is closed in the same feature that makes it a live risk, rather than left
for an incident to force later.

**Made harder.** Three more keys in an already-large frozen `TypedDict`, and
`graph_version`'s comparison logic is new machinery this feature must build and test even
though no version bump exists yet to exercise the restart path end-to-end — only the
single-version, no-mismatch case is testable today.

**Foreclosed.** No future feature may invent a fourth ad-hoc mechanism for recording why
a conversation escalated; `escalate_reason`'s closed set is the one place that lives, and
extending it (not replacing it) is the expected pattern for a new escalation trigger
later.
