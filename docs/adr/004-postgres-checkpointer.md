# ADR-004 — Postgres checkpointer over Redis / in-memory, in both environments

**Status**: Accepted
**Date**: 2026-09-09
**Feature**: 001-contracts-foundation

## Context

LangGraph (ADR-002) needs a checkpointer to persist `ResortFlowState` across supersteps, survive
process restarts, and support the `interrupt()`-based human-approval pause (ADR-006).
`spec.md`'s Assumptions record that "the workflow checkpoint store is the same technology
in local and hosted environments" as an already-taken decision this feature must honor, and
this feature's `hold_ledger` (ADR-019) is deliberately co-located with the checkpoint
tables in the same Postgres database as separate schemas of record — a choice this
checkpointer decision has to be compatible with.

## Options Considered

**1. In-memory checkpointer (`MemorySaver`).** Zero setup, fastest for unit tests — this
feature's own `tests/test_reducers.py` uses it precisely because User Story 1's Independent
Test explicitly requires no external system. Loses everything on process restart, so it is
categorically unsuitable for anything beyond tests: a guest with a hold in flight cannot
survive a crash under this option.

**2. Redis checkpointer.** Fast, already in the local topology for caching and hold timers,
and LangGraph has community checkpointer support for it. Redis's default persistence
guarantees (RDB snapshots / AOF) are weaker than a transactional RDBMS for the specific
property this feature depends on — durable, point-in-time-consistent writes that a
write-ahead ledger's crash-window reasoning (ADR-019) can be built against with confidence.

**3. Cosmos DB.** Has an official LangGraph checkpointer with Entra ID auth — a strong,
first-party Azure option for the production environment specifically.

**4. Postgres, same engine in both environments.** `langgraph-checkpoint-postgres`
locally via Docker, Azure Database for PostgreSQL Flexible Server (B1ms, ~$13/month) in
production — identical connection semantics, identical SQL, identical `EncryptedSerializer`
wrapping.

## Decision

**Postgres, the same engine locally and in Azure.** Cosmos DB's official checkpointer is a
genuine, strong alternative for the production side alone, but it is rejected here because
**engine parity between local and cloud was judged worth more than consolidating
datastores.** `docs/ResortFlow-HITL-Durability-State.md`'s own framing is direct: a config-parity
system where "identical code paths local and cloud; only connection strings and the model
provider differ" is a stated design principle (plan.md §11) — introducing Cosmos DB in
production only, with Postgres in Docker locally, would mean a behavior that only reproduces
in Azure is *by definition* a wrong abstraction, per that same principle. Postgres also lets
the write-ahead `hold_ledger` (ADR-019) live in the same database as the checkpoint tables,
as two deliberately separate schemas of record, without adding a second datastore to operate.

## Consequences

**Made easier.** Every developer tests against the exact database engine production uses;
a bug that "only happens in Azure" is structurally less likely. `EncryptedSerializer` and
the DDL for `hold_ledger` are written once and apply identically in both environments.
Local cost is zero (Docker); production cost is small and predictable (~$13/month at B1ms).

**Made harder.** Forfeits Cosmos DB's officially maintained checkpointer and its native
Entra ID auth story — Postgres auth has to be handled with a more conventional
credential/connection-string approach (Key Vault-sourced, per
`docs/ResortFlow-HITL-Durability-State.md`) rather than an Azure-native
identity flow.

**Foreclosed.** Cosmos DB's global-distribution and multi-region-write capabilities are not
available to the checkpointer without revisiting this decision. If ResortFlow ever needed
multi-region active-active conversation state, Postgres Flexible Server's replication model
would need real evaluation against Cosmos DB's, not just a configuration change.
