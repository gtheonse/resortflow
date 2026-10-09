# Interface Contract: `ResortFlowState` Amendment (feature 010)

**Feature**: `010-tiered-intent-routing` | Amends `docs/contracts/state-schema.md` (feature 001,
frozen) and `specs/005-multi-agent-orchestration/contracts/state-schema-amendment-005.md`,
additively only, per ADR-056.

Neither earlier document is edited. This is the additive amendment.

## New keys

| Key | Type | Default | Set by | Read by |
|---|---|---|---|---|
| `routing` | `RoutingDecision \| None` | `None` | `INTENT_ROUTER` (only) | `_route_from_intent_router`, `DETERMINISTIC`, `PLANNER`, `BOOKING_LOOKUP`, `MODIFY_REQUEST`, next turn's `INTENT_ROUTER` |
| `turn_baseline` | `TurnBaseline \| None` | `None` | `INTENT_ROUTER` (only) | reply composer, next turn's `INTENT_ROUTER` |

Types: [data-model.md](../data-model.md). Neither key has a reducer. Each has exactly one writer
node and that node never runs twice in one superstep.

## `escalate_reason` vocabulary: appended value

- `"modify_request"`: the guest asked to change or cancel a booking; `MODIFY_REQUEST` handed off.

Existing values keep their meaning (ADR-023: append, never repurpose).

## `GRAPH_VERSION`

`"5.0"` → `"5.1"`. **Minor** on purpose: `_route_from_entry` releases holds and restarts only on a
major mismatch, and FR-027 requires pre-010 conversations to resume.

## Non-negotiable constraints

- A pre-010 checkpoint with neither key deserializes cleanly and resumes. Both keys read as `None`.
- `RoutingDecision` and `TurnBaseline` (and the enums they contain: `RequestKind`,
  `RoutingTier`, `Tier2Outcome`) are registered in the checkpoint serializer's msgpack allowlist.
  A test round-trips a state containing both through `EncryptedSerializer` **and** `plain_serde()`
  with no pickle fallback.
- Neither key contains tenant, guest or message text. `tenant_id` remains the token-claim value
  set at thread start (AGENTS.md hard rule).
- `routing` from turn N is readable by turn N+1's `INTENT_ROUTER` **before** it is overwritten.
  That is the only cross-turn use.

## What this contract does not cover

How the router computes a decision ([router-api.md](router-api.md)) or how the reply uses the
baseline ([turn-replies.md](turn-replies.md)).
