# ADR-022 — Hold-ledger reaper: a standalone scheduled job, not an in-process loop

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 005-multi-agent-orchestration

## Context

ADR-019 designed the write-ahead hold ledger and its reaper conceptually but explicitly
scoped the reaper itself out: "not built by this feature — the schema and its
`(status, expires_at)` index are." `graph/models.py`'s `HoldStatus` enum already includes
`ORPHAN_RELEASED` as a terminal status, but `graph/ledger.py`'s `HoldLedger` Protocol has
no method to reach it — only `mark_held`, `mark_confirmed`, `mark_released`,
`mark_expired` exist. This feature is the first to run multiple domains concurrently in
one fan-out superstep, which multiplies the crash surface ADR-019 was written for by the
number of domains in a given itinerary (up to five, versus dining's one). Closing the
reaper gap is no longer optional once concurrent holds across domains are a normal case
rather than a theoretical one.

## Options Considered

**1. An in-process background task inside the gateway (or supervisor) process** —
`asyncio.create_task`, sleeping 60 seconds, querying `hold_ledger` directly. Zero new
deployable surface. Tied to that process's own lifecycle: restarting the process resets
the reaper's schedule, and running more than one replica of that process (a reasonable
production scaling need) means multiple reapers racing over the same rows unless
additionally guarded — a coordination problem this option creates rather than solves.

**2. A standalone scheduled job**, run independently of any request-serving process. Adds
one new deployable artifact, but decouples reaper cadence entirely from the scaling and
restart behavior of the gateway or supervisor, and is testable in isolation as a plain
script against a fake or real Postgres.

**3. Push the logic into Postgres itself** — a `pg_cron`-scheduled function, or a
`LISTEN`/`NOTIFY` plus trigger-based expiry path. Elegant in principle and consistent
with the project's "determinism first" bias, but adds a Postgres extension
(`pg_cron`) not currently part of the stack, and moves business logic — what counts as
"orphaned," when to probe the vendor versus release directly — into SQL, which is harder
to unit-test and trace than a plain Python job the existing telemetry module can
instrument.

## Decision

**Option 2.** A standalone scheduled reaper (`infra/jobs/hold_ledger_reaper.py`), run on
a 60-second cadence independent of the gateway or supervisor process's lifecycle — a
lightweight loop wrapper under docker-compose for local development, and a real scheduled
job resource (a container-apps job or equivalent) in the target deployment. As part of
this same decision, `HoldLedger`'s Protocol gains one additive method,
`mark_orphan_released(idempotency_key)`, with the same idempotent-write guarantee every
other ledger mutation already carries — this is a change to a frozen interface
(`graph/ledger.py`, standing since feature 001) and is exactly the kind of interface
change Constitution's Contract Stability standard requires an ADR for, even though the
change is purely additive.

## Consequences

**Made easier.** Orphan reclamation becomes testable in isolation, independent of
whatever else the gateway or supervisor process is doing, with one place to tune cadence
or add backoff. The crash-window story ADR-019 already documented (probe-then-release for
the two indistinguishable-at-the-schema-level windows, direct release for the third) now
has an actual implementation to point at instead of a designed-but-unbuilt gap.

**Made harder.** One more small deployable artifact to run and monitor, in both local
development and production. Local docker-compose has no native scheduled-job primitive,
so the reaper needs its own lightweight sleep-loop wrapper there rather than reusing
whatever scheduling exists in the target cloud deployment.

**Foreclosed.** The reaper stays domain-agnostic by construction — it queries
`hold_ledger` directly and never a domain-specific vendor API, except where the
crash-window logic itself requires probing the vendor by idempotency key before
releasing. No domain may grow its own bespoke reaper; this is the one.
