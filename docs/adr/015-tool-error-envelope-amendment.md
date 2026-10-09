# ADR-015 — Amend the frozen tool-error envelope for composite and indeterminate outcomes

**Status**: Accepted — confirmed by the user and applied to
`docs/contracts/schemas/tool-error.schema.json` on 2026-09-11. See `research.md` D6 and
`contracts/tool-error-envelope-amendment.md` for the diff as applied.
**Date**: 2026-09-11
**Feature**: 003-mcp-tool-layer

## Context

Two requirements this feature's own spec settled during `/speckit-clarify` need somewhere to
put data the frozen `tool-error.schema.json` has no room for
(`"additionalProperties": false` over a fixed field set):

- **FR-030**: a composite tool's partial failure must identify every sub-call that already
  completed, so nothing that actually took effect is silently orphaned.
- **FR-037**: a mutating call's indeterminate outcome (retries exhausted mid-commit while the
  upstream's failure protection was open, or a schema-invalid response to a mutating call)
  must be distinguishable from an ordinary failure that definitely did not touch vendor state.

Neither fits inside the existing four-class `error_class` enum without overloading its
meaning, and neither belongs in the free-text `detail` field (encoding machine-actionable
state in prose is exactly what the constitution's structured-over-free-text stance exists to
prevent). The natural alternative home — `hold_ledger.status` — is a *different* feature's
frozen artifact, already materialized at `infra/sql/001_hold_ledger.sql`, owned by whichever
future feature writes to it (Track C, Phase 4–5), not this one (`research.md` D6).

## Options Considered

**1. A companion table this feature owns** (e.g. `mcp_unresolved_effects`). Avoids touching
any frozen file. But gives a stateless tool layer a database it has no other reason to hold,
and creates a second place a future reconciler has to check in addition to `hold_ledger`
itself — solving a contract-freeze problem by creating a data-duplication problem.

**2. Encode the signal in the existing `detail` string.** No schema change at all. Rejected —
parsing prose to decide whether a database write should happen is precisely the anti-pattern
the constitution's "never carry [structured data] only in free text" principle targets,
applied here to an operational signal instead of a dietary one.

**3. Amend `tool-error.schema.json` with two new optional fields** — `completed_sub_calls` and
`effect_status`, both additive, both backward-compatible (no existing field, requirement, or
enum value changes). Puts the signal exactly where a consumer already has to look (the failure
envelope it already receives) instead of a second location.

## Decision

**Option 3.** Add `completed_sub_calls` (nullable array, present only for a composite tool's
partial failure) and `effect_status` (nullable enum `["unresolved"]`, present only when FR-032
or FR-035-on-a-mutating-call applies) to `tool-error.schema.json`. Diff as applied:
`contracts/tool-error-envelope-amendment.md`.

**This ADR was drafted Proposed and raised as a blocker before any edit was made**, per the
working agreement — "Phase 0 contracts are frozen... raise it as a blocker, do not edit
unilaterally." The user confirmed the amendment; it was then applied to
`docs/contracts/schemas/tool-error.schema.json` and this ADR moved to Accepted in the same
change.

## Consequences

**Made easier.** A future reconciler (Phase 4–5) has one envelope to read, not two data
sources to reconcile with each other. The signal is captured at the moment this feature
already knows it applies, rather than needing reconstruction later from data nobody thought
to keep.

**One honest qualification, found during this feature's `/speckit-analyze` review.** That
"already knows it applies" reasoning holds fully for `effect_status`, which this feature really
does produce (FR-032's indeterminate commit outcome, FR-035's schema-invalid response to a
mutating call). It does **not** hold for `completed_sub_calls`: every composite tool in this
feature's roster combines read-only vendor calls, and a composite performing two or more
*mutations* is inherently cross-domain, which is saga work in a later phase. So half of this
amendment is forward-looking — a field added for a producer that does not exist yet, exercised
in this phase only against a synthetic two-slot mutating composite in test. That is a weaker
justification than the one originally written here, and it is recorded rather than quietly
left standing. It does not change the decision: the field is optional, additive, and costs
nothing to carry, whereas retrofitting it once a saga tool exists would mean amending the same
frozen schema a second time.

**Made harder.** This feature took on a dependency on a Phase 0 file changing underneath it —
`/speckit-implement` could not have fully completed FR-030/FR-037's envelope-carrying behavior
until this amendment landed. Had it been rejected, this feature would have needed a fallback
(most likely option 1, accepted as the lesser cost at that point).

**Foreclosed.** Nothing structural — both new fields are optional, so no future consumer is
forced to read them, and no past consumer of the un-amended schema breaks.
