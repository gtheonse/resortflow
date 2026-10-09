# ADR-007 — Per-sim SQLite state over in-memory-only or shared Postgres

**Status**: Accepted
**Date**: 2026-09-10
**Feature**: 002-simulation-ecosystem

## Context

The five simulators are stateful by design (`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §9,
principle 1: "Stateful, not stub. Real inventory that depletes. Holds that expire."), and
this feature additionally requires that state be resettable to a seeded baseline on demand,
without a process or container restart (spec.md FR-014, added via `/speckit-clarify`). Where
and how each sim keeps that state is not fixed by the frozen OpenAPI contracts — it's an
implementation decision this feature has to make, and it's one a later feature would have to
live with if made carelessly, since feature 002 (MCP layer) and feature 007 (eval) will both
be calling these sims and depend on their behavior under concurrent, repeated, resettable use.

## Options Considered

**1. In-memory only (a Python dict/object graph inside each sim process).** Zero
infrastructure, trivial to implement, resets to baseline are just "restart the process." But
a sim restart during manual poking or a demo silently loses all state with no signal, and
"holds state across calls" becomes indistinguishable from a stub the moment a process
happens to restart mid-session — weak evidence for the "stateful, not stub" design goal.

**2. Shared Postgres schema — one database, five sims' worth of tables, potentially the same
instance already hosting feature 001's checkpointer and `hold_ledger`.** Would allow
cross-sim querying and a single backup/ops story, and reuses infrastructure this project
already runs. But it couples five independently-owned, file-disjoint services (Track A) to
one shared server for a benefit nothing in this phase's scope needs, turns FR-014's per-sim
reset into a cross-sim operation with lock-contention risk, and — most importantly — risks
exactly the conflation principle V warns against: `hold_ledger` is ResortFlow's write-ahead record
of what *it* did to a vendor; putting vendor-side simulated state in the same database
invites someone to eventually join across them as if they were the same kind of fact.

**3. One SQLite file per sim, at `sims/<name>/data/state.db`.** No shared server, no
connection pool, no cross-sim coupling. Crash-survivable within a process (state outlives an
accidental restart, unlike option 1) while remaining trivially resettable (delete the file,
reseed) without needing a container restart, satisfying FR-014 directly.

## Decision

**Per-sim SQLite, per option 3.** It is the only option that is simultaneously
crash-survivable, cheaply resettable without a restart, and structurally incapable of being
confused with — or accidentally sharing infrastructure with — ResortFlow's own checkpointer and
hold ledger.

## Consequences

**Made easier.** Reset (FR-014) is "delete a file, rerun `seed.py`" — no distributed
transaction, no lock coordination, no shared-server dependency for a test/ops operation that
runs frequently in CI. Each sim can be developed, tested, and deployed with zero
inter-service database coupling, matching the file-disjoint track ownership this project
already commits to.

**Made harder.** No cross-sim querying (e.g. "show me every hold across all five vendors for
this guest") — not needed by this feature, and if a later feature wants it, it will need to
aggregate at the MCP/agent layer rather than with a join, which is arguably correct anyway
since a real integration could never join across five actual vendor databases either.
Operationally, five separate SQLite files are five separate things that could each go
missing or get out of sync in a multi-replica deployment — acceptable for a local-development
simulator's single-instance-per-sim topology, and explicitly out of scope for a production
scale concern this feature doesn't have.

**Foreclosed.** Vendor-side simulation state can never silently end up in the same database
as `hold_ledger` or the checkpointer, by construction — a future feature wanting that would
need an explicit ADR reversing this one, not a quiet migration.
