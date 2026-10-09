# ADR-019 — Write-ahead hold ledger, outside the checkpointer

**Status**: Accepted
**Date**: 2026-09-09
**Feature**: 001-contracts-foundation

**Note on numbering**: this decision is not in the ADR register at §15 of
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` (which runs 001–009). It is added here,
deliberately out of that original sequence, because it is this feature's single most
defensible decision and the register had no entry for it — see plan.md's "Recorded
deviations."

## Context

ADR-004 commits to Postgres as the LangGraph checkpointer, in both environments. But the
checkpointer's actual guarantees are narrower than they look. Under `durability="sync"`,
the persistence order of `put_writes` (a completed task's pending writes) and `put` (the
superseding checkpoint) is not enforced — both are submitted to the same background
executor thread pool, so whether a crash during `put()` leaves a task's writes durable is
decided by the OS thread scheduler. Separately, and independently dangerous: on resume,
LangGraph does not continue from the same line of code — it identifies a starting point and
*replays* all steps from there, re-running tasks and relying on recorded outcomes. Together,
those two facts mean a node that placed a vendor hold can re-execute after a crash. Without
an independent record of that hold's intent and outcome, a partial fan-out failure
double-books external inventory on every retry — a guest could hold the same table twice
under two different, unreconciled attempts.

This feature's `Hold` model already carries an `idempotency_key` that exists *before* the
vendor responds specifically because of this problem (data-model.md item 2). The question
this ADR answers is where the record of that key's lifecycle actually lives.

## Options Considered

**1. Derive external-effect state entirely from the checkpoint.** No new table, no new
write path — `ResortFlowState.holds` already carries the same information the checkpointer
persists. This is the failure this ADR exists to prevent: §11's failure matrix has a row
(crash under `durability="exit"`) where there is no checkpoint at all, and the ledger is
the *only* recovery path. Even under `sync`, the ordering issue above means the checkpoint
alone cannot be trusted as the sole record of an in-flight external mutation.

**2. A separate database for the ledger, apart from Postgres.** Cleaner isolation in
principle — a ledger outage can never be caused by a checkpoint-store problem and vice
versa. Forfeits transactional reasoning within one engine and doubles the operational
surface (a second database to provision, back up, and monitor) for a system of this
scale, for a benefit that mostly matters at a scale ResortFlow is not operating at.

**3. Event-source the whole saga** — treat every saga step as an event in an append-only
event store, and derive current ledger state by replay. Strictly more general than a
single-table ledger, and over-engineered for the volumes involved here: the append-only
`saga_log` already carried in `ResortFlowState` (and mirrored to Postgres) plus a dedicated
ledger table already provide full reconstruction without a separate event-sourcing
framework.

**4. A ResortFlow-owned `hold_ledger` table, in the same Postgres database as the checkpoint
tables, but a deliberately separate schema of record.** Written outside the checkpointer's
own write path: intent is recorded before the vendor call, outcome after, joined by
`idempotency_key` with a `UNIQUE` constraint that makes the reaper's job decidable.

## Decision

**A ResortFlow-owned `hold_ledger` table (option 4), co-located with the checkpoint tables in the
same Postgres database but never written through the checkpointer's own machinery.** The
conclusion that made this unavoidable: **the checkpointer is a system of record for
conversation state, not for external side effects.** Those are different durability
requirements and belong in different stores, even when those stores happen to share a
database engine.

Concretely, this closes three specific crash windows (documented in
`infra/sql/001_hold_ledger.sql`'s own header comment, and this feature's Independent Test
for User Story 2):

- **Crash after intent is recorded, before the vendor call** — `status='PENDING'`,
  `hold_id IS NULL`; a reaper probes the vendor by `idempotency_key` and releases if the
  hold exists.
- **Crash after the vendor call, before the outcome is recorded** — indistinguishable from
  the row above at the schema level, which is exactly why `idempotency_key` — never the
  vendor-assigned `hold_id`, which is null in both cases — is the join key across every
  window.
- **Crash after the outcome is recorded** — `status='HELD'` with a `hold_id` present; a
  reaper releases directly.

A reaper (not built by this feature — the schema and its `(status, expires_at)` index are)
runs periodically: any row in `PENDING` or `HELD` whose session has no active thread and no
terminal outcome is released and marked `ORPHAN_RELEASED`.

## Consequences

**Made easier.** Recovery from any of the three crash windows is a schema-level query, not
a bespoke reconciliation procedure per failure mode. The `UNIQUE` constraint on
`idempotency_key` makes duplicate-attempt collapse a database guarantee rather than
application logic that could drift. Ledger and checkpoint tables share operational
infrastructure (one database to back up, one connection pool) while remaining independently
reasoned-about schemas of record.

**Made harder.** Every mutating tool call now has two write obligations instead of one —
record intent, then record outcome — and both must happen even on the unhappy path. A
developer adding a new mutating tool has to remember the ledger exists; nothing forces it
structurally at the Python level within this feature (the MCP tool contract standard,
FR-015/FR-016, is where that gets enforced procedurally).

**Foreclosed.** No mutating tool may treat `ResortFlowState.holds` as sufficient evidence of an
external system's actual state on resume — AGENTS.md's hard rule ("Never trust
`state["holds"]` on resume. Revalidate against the vendor.") is a direct consequence of
this ADR, not an independent rule. Any future feature that wants to skip the ledger write
for a "simple" mutation needs an explicit justification, because the three-crash-window
argument above does not have a size threshold below which it stops applying.
