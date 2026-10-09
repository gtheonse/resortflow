# ADR-006 — Hold-then-confirm saga over direct booking

**Status**: Accepted
**Date**: 2026-09-09
**Feature**: 001-contracts-foundation

## Context

A single guest turn can touch three independent vendor systems with no shared transaction
— "dinner at 6:30, two tickets to the 8pm show, and a late checkout Sunday" — and none of
dining, ticketing, and the PMS participate in a two-phase commit with each other, nor ever
will (`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §6 is explicit on this). Failure modes are
concrete and already documented: tickets succeed and dining fails, dining succeeds and
tickets sell out mid-turn, everything succeeds and the guest changes their mind, or a
network partition mid-commit leaves an unknown state. This feature's `Hold`/`Confirmation`
models and the `HoldStatus` state machine (data-model.md) exist to make one of those
failure modes structurally survivable rather than a remediation ticket.

## Options Considered

**1. Direct booking — call each vendor's confirm/purchase endpoint immediately as each
domain resolves.** Simplest to implement per-domain: no intermediate state, no expiry
timers, no reconciliation logic. Every failure after the first successful booking becomes
an irreversible partial commitment requiring manual cleanup — "agent crashed mid-flow" is a
support ticket, not a self-healing condition.

**2. A distributed two-phase commit across all three vendor systems.** Would give the
strongest consistency guarantee if it existed, but none of the three vendor systems support
XA-style two-phase commit, and building a coordinator against unwilling participants is not
a real option — this is why the failure-mode analysis above states plainly that no shared
transaction exists "and never will."

**3. Hold → propose → approve → commit → verify → compensate.** Every domain first places
a reversible, time-boxed hold. A deterministic conflict check runs across all holds before
anything is shown to the guest. The guest approves (or partially approves) the *plan*, not
each booking individually. Only then does sequential commit begin, ordered riskiest-first.
Any partial commit failure triggers semantic compensation, not a mechanical rollback.

## Decision

**Hold-then-confirm, per option 3.** It converts an irreversible distributed transaction
into a reversible one: holds expire on their own, so "agent crashed mid-flow" self-heals —
inventory returns to the vendor's pool without a cleanup job. Direct booking makes every
crash a manual remediation ticket; hold-then-confirm makes most crashes a non-event.

Four decisions nested inside this pattern, each independently defensible:

- **Commit is sequential, not parallel, and ordered riskiest-first.** Parallel commit
  maximizes the window of partial success with no information about it. Sequential commit
  fails fast and localizes exactly where it stopped; ordering by scarcity and
  irreversibility (event tickets before a trivially re-requestable late checkout) means the
  expensive failure happens first, when compensation is cheapest.
- **Compensation is semantic, not mechanical.** A rollback engine would cancel everything
  on any partial failure — wrong for a guest who still wants dinner. Recovery's job is to
  preserve as much of the guest's intent as possible ("find another table," not "cancel the
  show"). This is one of the few places an LLM genuinely earns its place: "what's the best
  partial outcome here" is a judgment call, not arithmetic — consistent with this project's
  hard rule against putting an LLM where arithmetic will do, because this specifically
  isn't arithmetic.
- **Idempotency keys are non-negotiable on every mutating call.** Agents retry; networks
  partition; a confirm call that times out may or may not have succeeded. Every mutating
  tool takes a client-generated key derived from `(session_id, hold_id, attempt)` —
  contract enforcement, not test scaffolding (this is also why the frozen `Hold` model in
  this feature makes `idempotency_key` non-optional and never `hold_id`, which is null
  until the vendor responds).
- **Human approval sits exactly at the reversible/irreversible boundary, always.** Holds
  are reversible and cost nothing; commits move money. Earlier is annoying (approving
  before you know something is even available), later is dangerous (approving after money
  has moved). This is the only principled place to put the interrupt.

## Consequences

**Made easier.** A crash mid-hold is self-healing by construction — no cleanup job, no
manual remediation. Compensation has a real chance to preserve guest intent rather than
mechanically unwinding a whole plan over one domain's failure. Every downstream track
(MCP layer, domain subgraphs, recovery agent) inherits a single, consistent mutation
pattern rather than inventing its own per domain.

**Made harder.** The system now carries real state machinery that direct booking would not
need: hold TTLs, a conflict checker that must run before any commit, a saga log, and a
recovery agent with genuine judgment calls to make. Sequential riskiest-first commit is
slower in the success case than parallel commit would be — correctness is bought with
latency.

**Foreclosed.** No domain may ever move money or claim inventory in a single call — that
door is closed structurally (constitution Principle III), not by convention. A future
integration that genuinely cannot support a reversible hold (some vendors may not offer
one) requires an explicit exception via a new ADR, not a quiet bypass of this pattern.
