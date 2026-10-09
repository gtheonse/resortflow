# ADR-045 — Evaluation Run history, Baseline, and Coverage Exemptions stored as versioned repo files

**Status**: Accepted
**Date**: 2026-09-13
**Feature**: 007-eval-observability-gate

## Context

The gate needs somewhere to persist three things across CI runs, which are
otherwise stateless between invocations: past Evaluation Run reports (FR-017,
needed for regression comparison), the current Baseline (FR-007c), and active
Coverage Exemptions (FR-009a/025-029). This feature's own cloud-agnostic
requirement (FR-013) additionally demands the gate reach a verdict with zero
live cloud service calls.

## Options Considered

**1. Reuse the existing PostgreSQL checkpointer database.** Advantage: no new
storage technology introduced; infrastructure already exists and is already
running. Disadvantage: that database is scoped, by Constitution Principle V, to
conversation state under an explicit `EncryptedSerializer` contract — CI/eval
bookkeeping has nothing to do with guest conversations, and blurring that
boundary works against the principle's own separation of concerns. It also
means every CI runner needs live network access to a long-lived database
instance, which is exactly the kind of live dependency FR-013 is trying to
eliminate from the gate's critical path.

**2. Versioned repo files** (`eval/data/baseline.json`,
`eval/data/exemptions.yaml`, `eval/data/reports/<run-id>.json`), updated by an
automated commit from the merge-queue tier after a run reaches a COMPLETE
verdict on `main`. Advantage: no new infrastructure at all; a baseline change
is itself a reviewable diff; git history is the audit trail for free, directly
extending Constitution Principle V's "durable, inspectable" requirement to the
CI-state layer; works identically for any CI runner with no network dependency
beyond git itself. Disadvantage: concurrent merge-queue runs writing to the
same file need a serialization strategy (the merge queue's own one-at-a-time
semantics already provide this in practice, but it is a property this design
leans on rather than one it enforces independently).

**3. CI-platform-native artifact/cache storage** (e.g., GitHub Actions cache
or artifacts). Advantage: no repo commits from automation, keeps generated
state out of git history. Disadvantage: ties baseline continuity to one CI
platform's proprietary storage mechanism — a CI-platform lock-in that works
against this feature's own platform-agnostic design intent (ADR-046), even
though the specific concern here is CI-platform rather than cloud-vendor
lock-in, and cache entries are not code-reviewable the way a file diff is.

## Decision

**Adopt Option 2.** Baseline, exemptions, and run-history reports live as
versioned files under `eval/data/`, updated by automation only from the
merge-queue tier on `main`.

## Consequences

**Made easier.** No new database, service, or CI-platform-specific feature is
required; a baseline update or a new exemption is an ordinary, reviewable git
diff; any CI runner or a laptop can read the exact same files with no network
call.

**Made harder.** The automated baseline-update commit is itself a new kind of
automation this repository hasn't had before (a bot committing to `main`
outside a human-authored PR) and needs its own care around race conditions if
the merge-queue's serialization guarantee is ever weakened.

**Foreclosed.** This does not give a query-able, indexed history the way a
real database would — finding "the report from three weeks ago" means reading
git history on `eval/data/reports/`, not running a query. Acceptable for this
phase's scale; would need revisiting if report volume or query needs grow.

## Addendum — Feature 009 (2026-09-27)

Baseline writes now have **two** automation paths, not one:

1. The merge-queue commit on `main` (this ADR's original decision) — unchanged eligibility rule
   (`is_eligible_baseline`: merge-queue tier, COMPLETE verdict).
2. A label-triggered rebaseline commit on a **PR branch** (`.github/workflows/rebaseline.yml`,
   ADR-054), authenticated with a fine-grained PAT (`REBASELINE_TOKEN`) rather than `GITHUB_TOKEN`
   so the push re-triggers the PR gate.

`Baseline` also gained `judge_prompt_hashes` and per-tenant mean/stdev maps (`eval/models.py`,
T048) — both are additive fields with empty-dict defaults, so a baseline file written before this
feature still loads (`eval/baseline.py::load_baseline`), and is treated as `judge_prompt_status:
absent` rather than `ok` (FR-021) until the first rebaseline populates them. The versioned-repo-file
choice this ADR made is exactly what makes a second writer straightforward to add: it's still an
ordinary git commit, just from a different workflow.
