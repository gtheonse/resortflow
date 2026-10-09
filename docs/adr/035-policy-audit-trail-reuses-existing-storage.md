# ADR-035 — Policy audit trail reuses `policy_flags` + `saga_log`; no new storage

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

Every policy decision this feature makes needs to be reviewable after the fact — FR-014
requires a human reviewer to identify a specific blocked item and its reason, and
User Story 5's independent test requires that be checkable without a new console. The
project already has two append-only, Postgres-mirrored records of "what happened during
this conversation": `ResortFlowState.saga_log` (existing, `SagaEvent`, already mirrored to
Postgres per its own docstring — "so compensation survives process death") and, as of
ADR-029, `ResortFlowState.policy_flags` (existing field, now with a working reducer). ADR-027
(feature 005) already established the pattern of reusing an existing mirror rather than
building a new table for a new kind of event ("failed-compensation dead-letter path
reuses the existing `saga_log` Postgres mirror, not a new table").

## Options Considered

**1. A new `policy_audit` Postgres table**, purpose-built for policy decisions with its
own schema (block reason, item reference, resolution state, timestamps), separate from
`saga_log`/`policy_flags`. Gives a dedicated schema tuned exactly to policy queries.
Rejected: this is a second place "what happened" lives, splitting an auditor's view of
one conversation across three sources (checkpoint, `saga_log`, and now a fourth table)
for no query pattern this feature actually needs yet — ADR-027 rejected exactly this
shape of proposal for the same reason one feature ago.

**2. Reuse `policy_flags` (the compliance determination: rule, disposition,
jurisdiction, timestamp) plus a `SagaEvent` (the narrative: what proposal/domain it
applied to, sequence in the saga) — no new table.** Matches the existing division of
labor: `PolicyFlag` already exists specifically to be "a compliance determination
attached to a conversation, recording what was enforced and why" (its own docstring);
`SagaEvent` already carries the domain/item-level narrative context every other kind of
saga event uses.

## Decision

**Option 2.** No new table, no new Postgres migration. Every policy block/escalation
this feature produces writes one `PolicyFlag` (the "what rule, what disposition, what
jurisdiction" record) and, where a specific item is involved (a proposal, a domain), one
`SagaEvent` (the "which item, in which saga" record) — the same two-record pattern
`agents/dining/nodes/allergen_check.py` already uses today for its own blocking case
(`SagaEvent(kind="dietary_conflict_flagged")`), now paired with the `PolicyFlag` it was
missing.

## Consequences

**Made easier.** A human reviewer (or a future eval/analytics feature) has exactly two
places to look for "what happened and why" in any conversation, regardless of whether
the event was a saga commit step or a policy decision — no third destination to learn.
No new Postgres migration, no new index, nothing added to `infra/sql/`.

**Made harder.** `policy_flags` and `saga_log` together carry a slightly wider variety of
event shapes than either did alone — acceptable, since both were already general-purpose
append-only records, not single-purpose ones.

**Foreclosed.** No future feature may introduce a new "what happened" table for a new
kind of conversation event without first showing why `saga_log`/`policy_flags` (or a
targeted amendment to one of them, per the ADR-023/ADR-029 pattern) genuinely can't
represent it — this is now the second feature in a row to apply this reuse-over-new-table
default (ADR-027, this ADR), making it the project's established pattern, not a one-off.
