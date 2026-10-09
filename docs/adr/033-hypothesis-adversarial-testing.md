# ADR-033 — `hypothesis` adopted for adversarial/property-based policy tests

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

This phase's exit criteria are adversarial by construction: a self-excluded guest must
receive zero gaming-adjacent content across a 40-turn script; an injection corpus sweep
must change zero tool-invocation decisions; a redaction pass must leave zero sensitive
fields exposed across a representative sample. `AGENTS.md`'s working agreement already
names "saga failure permutations" and "tenant isolation" as mandatory test categories
requiring more than example-based unit tests — the same reasoning applies here: a
handful of hand-picked examples proves the happy path and a few chosen edge cases, not
the absence of a counterexample across the input space these guarantees claim to cover.
`pyproject.toml` has no property-based testing library today (`mypy`, `pytest`,
`pytest-asyncio`, `ruff`, `openapi-spec-validator`, `schemathesis` are the current dev
dependencies).

## Options Considered

**1. Hand-written example tests only**, scaled up by adding more cases by hand (more
adversarial script variants, more injection corpus entries, more redaction fixtures).
No new dependency. Rejected as the sole approach: this project already has 200
hand-written adversarial cases for one thing (`rag/eval/adversarial_set.yaml`'s
cross-tenant probes) and it is exactly the kind of corpus that catches only what someone
thought to write down — a generated, randomized input space finds the case nobody
thought of, which is precisely the risk profile of "an allergen check that has a subtle
off-by-one on severity comparison" or "a redaction regex that misses a rare name
format."

**2. Adopt `hypothesis`** for the policy module's property tests specifically (dietary-
fidelity comparison, self-exclusion gating, redaction field-allowlist enforcement),
generating varied structured inputs (restriction/severity combinations, guest-status
permutations, payload shapes) rather than enumerating them by hand, alongside the
existing hand-written adversarial corpora (which remain — this is additive, not a
replacement for the 40-turn script or the injection corpus, both of which are meaningful
specifically because they're realistic guest-shaped scripts, not random data).

## Decision

**Option 2.** Add `hypothesis` as a new dev dependency. Scope: `policy/tests/` uses it
for the properties that are genuinely about input-space coverage over structured data
(e.g., "for any `DietaryRequirement` combination, a restriction present in conversation
but absent from the structured payload always produces `fidelity_ok=False`"; "for any
`ResponsibleGamingStatus` with `self_excluded=True`, `require_gaming_clear` always
returns `permitted=False`"). The realistic, scripted adversarial corpora (40-turn
self-exclusion script, injection corpus) remain hand-authored — `hypothesis` complements
them, it does not generate guest conversations.

## Consequences

**Made easier.** The dietary-fidelity and self-exclusion gating properties get the same
"same inputs always produce the same, correct output" confidence `graph/errors.py:
classify()`'s determinism already has, verified over a generated input space rather than
whatever cases a test author happened to think of.

**Made harder.** One more dev dependency; `hypothesis` tests need a `@given` strategy
authored per property, a small new pattern for contributors to learn (though a common,
well-documented one).

**Foreclosed.** Nothing structural — this is additive tooling, not an architectural
commitment. A future feature is free to use or not use `hypothesis` for its own property
tests on its own merits.
