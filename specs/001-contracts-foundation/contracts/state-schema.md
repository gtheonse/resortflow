# Interface Contract: `ResortFlowState`

**Feature**: `001-contracts-foundation` | Permanent home: `graph/state.py`

This is the canonical conversation state every domain subgraph reads and writes. It is a
`TypedDict` so that LangGraph can bind reducers to individual channels via `Annotated`
metadata — a `BaseModel` state has no per-channel reducer mechanism, which is why the state
container and the entities inside it use different modeling tools (see
`research.md` item 4).

## Frozen key set

See `data-model.md` → "`ResortFlowState` — the frozen key set" for the full table of 20 keys and
their reducers. This contract additionally specifies reducer *behaviour*, which is what
`test_reducers.py` verifies.

## Reducer contracts

Every reducer below must satisfy three properties, tested explicitly:

1. **Accepts an absent left value.** The first write to a channel in a fresh conversation
   must not error.
2. **Idempotent under replay.** Applying an identical right-hand contribution twice must
   leave the result identical to applying it once. This is not optional — LangGraph resume
   replays steps, so any reducer that isn't idempotent double-books on every resume.
3. **Loses nothing contributed in the same superstep.** Three concurrent writers must all
   be represented in the result.

### `merge_proposals(left, right) -> list[Proposal]`

Merges by `proposal_id`. On a collision, the right (newer) value wins. Items new to the
right side are appended, preserving left-side ordering for everything already present.

### `merge_holds(left, right) -> list[Hold]`

Merges by `idempotency_key` — **never** by `hold_id`, which is `None` until the vendor
responds and therefore cannot identify an in-flight attempt.

Terminal-state precedence: if the left value's `status` is one of `confirmed`, `released`,
`expired`, `orphan_released`, the right value MUST NOT overwrite it with `pending` or
`held`. See the state machine in `data-model.md`.

### `append_only(left, right) -> list[T]`

Concatenation. Used for `confirmations`, `failures`, `saga_log`. Never rewrites an existing
element.

### `dedupe_by_id(left, right) -> list[Chunk]`

Deduplicates by `chunk_id`. First occurrence wins, so relevance rank order from the RRF
fusion step survives the merge.

## Non-negotiable constraints (from the constitution and the spec)

- `tenant_id`, `guest_id`, `session_id`, `jurisdiction` are set once at conversation entry
  from a validated token claim. No reducer, node, or downstream code may derive or mutate
  them from model output.
- `replan_count` has a hard bound of 3. A node incrementing it past the bound must set
  `escalate = True` rather than continue the cycle.
- Every value reachable from `ResortFlowState` must serialize under `JsonPlusSerializer` without
  the pickle fallback. If a field requires a type `JsonPlusSerializer` cannot express
  natively, the field is wrong, not the serializer.
- The key set itself is frozen. Adding, removing or renaming a key is a breaking change
  requiring an ADR — never a routine edit.
