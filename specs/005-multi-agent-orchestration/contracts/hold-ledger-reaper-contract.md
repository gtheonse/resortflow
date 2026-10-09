# Interface Contract: Hold-Ledger Reaper

**Feature**: `005-multi-agent-orchestration` | Extends: `graph/ledger.py`'s `HoldLedger` Protocol (feature 001/004, frozen) — additively, per ADR-022

## `HoldLedger` Protocol amendment

One new method, additive to the existing five (`insert_pending`, `mark_held`,
`mark_confirmed`, `mark_released`, `mark_expired`):

```python
async def mark_orphan_released(self, *, idempotency_key: str) -> None: ...
```

Same idempotent-write guarantee as every other method: a duplicate call against an
already-terminal row is a no-op, never an error. Transitions a `hold_ledger` row's
`status` to `ORPHAN_RELEASED` (already defined in `graph.models.HoldStatus` and
`TERMINAL_HOLD_STATUSES` — this feature adds the ledger-side method to reach a status the
data model already anticipated).

## Reaper contract

- **Cadence**: every 60 seconds (matches the design already stated in
  `ResortFlow-HITL-Durability-State.md` §3 and ADR-019).
- **Query**: any `hold_ledger` row in `PENDING` or `HELD` whose associated `saga_id` has
  no active thread (no in-progress checkpoint for that thread) and no terminal outcome
  recorded.
- **Crash-window disposition** (verbatim from ADR-019, now with an actual implementation):

  | Row state found | Reaper action |
  |---|---|
  | `PENDING`, `hold_id IS NULL` | Probe the vendor by `idempotency_key`; if a hold exists there, release it via the domain's `release_*` tool, then `mark_orphan_released`. If no hold exists, `mark_orphan_released` directly. |
  | `HELD`, `hold_id` present | Release directly via the domain's `release_*` tool using the recorded `hold_id`, then `mark_orphan_released`. |

- **Process**: `infra/jobs/hold_ledger_reaper.py`, run independently of the gateway or
  supervisor process (ADR-022) — a docker-compose sidecar loop locally, a scheduled job
  resource in the target deployment.
- **Idempotent by construction**: running the reaper twice concurrently over the same row
  is safe — the `mark_orphan_released` write only succeeds from `PENDING`/`HELD`, and a
  second attempt against an already-`ORPHAN_RELEASED` row is a no-op per the Protocol's
  own guarantee.

## What this contract does not cover

How "no active thread" is determined precisely (a join against the checkpointer's own
thread table, or a simpler time-since-`updated_at` heuristic) is an implementation detail
left to the reaper's own code and tests, not a schema-level contract. Domain-specific
`release_*` tool call shapes are each domain's own MCP tool contract
(`mcp-tool-contract.md`, and `entertainment-mcp-tool-contract.md` for the one domain this
feature adds).
