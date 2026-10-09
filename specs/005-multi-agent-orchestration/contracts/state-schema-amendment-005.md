# Interface Contract: `ResortFlowState` Amendment (feature 005)

**Feature**: `005-multi-agent-orchestration` | Amends: `docs/contracts/state-schema.md` (feature 001, frozen) — additively only, per ADR-023

`state-schema.md` is not edited in place — feature 001's contract stays frozen exactly as
written. This document is the additive amendment feature 005 makes to it, per the
Contract Stability standard ("interfaces frozen in an earlier feature MUST NOT be changed
by a later one without an ADR" — see `docs/adr/023-resortflowstate-amendment-feature-005.md`
for the full decision).

## New keys

| Key | Type | Default | Set by | Read by |
|---|---|---|---|---|
| `graph_version` | `str` | *(none — required at thread start)* | Supervisor entry, from a build-time constant | Resume path (version-mismatch check) |
| `clarification_count` | `int` | `0` | `CLARIFY` | `CLARIFY` (bound check), `HUMAN_HANDOFF` |
| `escalate_reason` | `str \| None` | `None` | `CLARIFY`, `PLANNER`, `RECOVERY` | `HUMAN_HANDOFF` |

## `escalate_reason` closed vocabulary

Exactly one of, set exactly once per conversation:

- `"replan_bound"` — `replan_count` exceeded `MAX_REPLAN_COUNT`.
- `"clarification_bound"` — `clarification_count` exceeded `MAX_CLARIFICATION_COUNT`.
- `"compensation_failed"` — `RECOVERY` could not find or apply a compensating alternative.

A future feature extending this vocabulary appends a new value; it MUST NOT repurpose an
existing one, per ADR-023's foreclosed consequence.

## Non-negotiable constraints (mirrors `state-schema.md`'s existing format)

- All three keys are additive: an existing checkpoint from feature 004 with none of these
  keys present deserializes cleanly, since every new key has either a required
  thread-start assignment (`graph_version`, set fresh on every new thread — feature 004
  threads that resume do not retroactively gain a version and are handled by the
  cross-major-resume rule below) or a default (`clarification_count`, `escalate_reason`).
- `graph_version` comparison on resume: if the resumed thread's stored `graph_version`
  major component differs from the current build's, the conversation does not continue —
  outstanding holds are released and the guest is asked to restart
  (`ResortFlow-HITL-Durability-State.md` §8 mitigation #5). A thread with no `graph_version` at
  all (pre-005) is treated as major version `0` and always triggers this path once this
  feature ships.
- `clarification_count` and `replan_count` are independent counters. A conversation may
  exhaust both bounds across its lifetime; either bound alone is sufficient to trigger
  `HUMAN_HANDOFF`.

## What this contract does not cover

The actual `graph_version` value scheme (semver string, integer, build hash) is an
implementation detail of the supervisor's entry node, not a schema-level concern beyond
"it is a `str`, compared for a major-version-equivalent mismatch." This document does not
mandate a specific versioning scheme, only the field's presence, type, and comparison
behavior.
