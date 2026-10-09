# ADR-027 — Failed-compensation dead-letter path reuses the saga_log Postgres mirror

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 005-multi-agent-orchestration

## Context

Constitution Principle III requires: "A compensation that itself fails MUST
write the full saga log to a dead-letter path and escalate to a human. Silent
partial failure is the only unacceptable outcome." `docs/ResortFlow-Enterprise-
Multi-Agent-Plan.md` §6 elaborates the same requirement narratively:
compensation failures "write to a dead-letter queue with the full saga log,
page the on-property duty manager with a structured summary." Neither
document, nor this feature's own `data-model.md`, says what the "dead-letter
path" actually *is* — `graph.models.SagaEvent` is already append-only and
mirrored to Postgres "so compensation survives process death" (its own
docstring), which is a candidate, but nothing states whether that mirror
*is* the dead-letter path or whether a dedicated store is still needed.
Left unresolved, two engineers implementing `RECOVERY`'s compensation-failure
branch (`graph/supervisor.py`, T020) would reasonably build two different
things.

## Options Considered

**1. Reuse the existing Postgres-mirrored `saga_log`**, identifying failed
compensations by `escalate_reason="compensation_failed"` (ADR-023) joined to
`saga_id`. Zero new schema. "The dead-letter queue" becomes a query, not a
queue with delivery/consumption semantics — no claim, no ack, no visibility
timeout, no retention policy distinct from ordinary saga log retention.

**2. A dedicated `dead_letter_queue` table**, one row per failed-compensation
saga, with its own status column (`pending_review` → `resolved`) a future
duty-manager tool could claim and acknowledge. Gives real queue semantics,
but is new schema and infrastructure this feature would have to design with
no real consumer to design it against — no duty-manager console or paging
integration exists anywhere in this project yet.

**3. An external paging/message-queue integration** (e.g., a real on-call
paging service). Explicitly out of scope: "page the on-property duty manager"
is live-human-handoff-console work, already deferred to Phase 6+ by ADR-020's
same reasoning (`OFFER_AGENT`'s Policy Engine dependency isn't built yet
either).

## Decision

**Option 1.** The dead-letter path this feature satisfies Constitution
Principle III with is the existing Postgres-mirrored `saga_log`, filtered by
`escalate_reason="compensation_failed"`. No new table, no new
infrastructure. A real duty-manager-facing queue with claim/ack semantics is
a decision for whichever future feature actually builds the live human
handoff console (Phase 6+) — that feature will know its real consumption
pattern; guessing at one now would very likely guess wrong.

## Consequences

**Made easier.** Zero new schema to design, migrate, or maintain.
`escalate_reason` (ADR-023) already gives the dead-letter path its query
filter — no separate mechanism needed.

**Made harder.** No queue semantics exist yet: nothing prevents two staff
members from working the same failed compensation simultaneously, and there
is no retention policy distinct from whatever `saga_log`'s own retention
already is. This is an accepted gap until a real consumer exists, not an
oversight.

**Foreclosed.** No future feature may treat `saga_log` as a stateful queue
with per-consumer claims bolted onto it after the fact — when real queue
semantics are needed, that is a new, explicit decision (and likely a new
table), not an incremental extension of this one.
