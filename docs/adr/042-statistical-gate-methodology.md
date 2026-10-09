# ADR-042 — Statistical gate methodology: separated floor and delta statistics

**Status**: Accepted
**Date**: 2026-09-13
**Feature**: 007-eval-observability-gate

## Context

The evaluation gate must distinguish two failure modes for a statistical
metric (retrieval quality, faithfulness, trajectory quality, cost/latency): an
absolute floor violation, and a regression from the current main-branch
baseline that stays above the floor. Judge-model scoring is inherently noisy;
naively comparing single raw scores on both checks would make the gate flaky.
The spec's `/speckit-clarify` session worked through this in detail (Q1–Q4,
2026-09-13) and arrived at a specific, non-obvious design: the floor check uses
a variance-adjusted score computed from 3 repeated runs (mean − 1σ), while the
delta-vs-baseline check compares **raw means** on both sides, with the "what
counts as a regression" margin itself widened by the baseline's own variance
(`max(fixed %, 2 × σ_baseline)`) rather than by adjusting either side's value.

The initial draft of this design (recorded verbatim in the Clarifications
section) used the variance-adjusted score on both checks. That was corrected
during clarification: applying a one-sided confidence discount to both operands
of a two-sided comparison silently imports the baseline run's own variance into
every verdict, reintroducing the exact flakiness the discount was invented to
remove.

## Options Considered

**1. Single raw score, no variance adjustment anywhere.** Advantage: simplest
possible implementation. Disadvantage: judge-model scoring noise alone can flip
a gate's verdict run to run, which is the flaky-CI failure mode this entire
design exists to prevent.

**2. Variance-adjusted (mean − 1σ) score used identically for both the floor
check and the baseline-delta check.** Advantage: one formula, reused everywhere,
easy to explain. Disadvantage: statistically incorrect for the delta check —
it double-discounts variance (once via the −1σ adjustment, again via whatever
margin covers comparison noise), and using an already-shifted value on both
sides of a before/after comparison can manufacture or mask a regression
depending on which run happened to sample lower.

**3. Two distinct statistics for two distinct questions**: variance-adjusted
score against a fixed floor (a one-sided "are we still above the bar"
question, where a one-sided confidence discount is the right tool), and raw
mean-vs-mean against a variance-aware margin for the delta check (a two-sided
"did this get worse" question, where the comparison's own tolerance — not the
compared values — should carry the noise budget). Advantage: statistically
sound; each check uses the statistic suited to the question it is actually
asking. Disadvantage: two formulas to implement and explain instead of one,
and it requires storing both the mean and the standard deviation on every
Baseline, not just a single number.

## Decision

**Adopt Option 3.** The absolute-floor check (FR-007a) evaluates the 3-repeat
variance-adjusted score against a fixed floor. The baseline-delta check
(FR-007b/c) evaluates the current run's raw mean against the baseline run's raw
mean, with the regression margin set to `max(fixed %, 2 × σ_baseline)`. A
baseline is only eligible if it reached a COMPLETE verdict and carries a
judge-model identity matching the current run; a mismatch reports
`BASELINE_INVALID` for the affected metric rather than a false regression.

## Consequences

**Made easier.** The gate does not confuse "the model doesn't clear our
minimum bar" (a floor problem) with "this got worse than it used to be" (a
trend problem) — each is diagnosable independently in the report, and a judge
model upgrade cannot masquerade as a quality regression.

**Made harder.** The Baseline record must persist per-metric standard
deviation alongside the mean (not just a single number), and any future
statistical-gate metric added to the suite must be reasoned about against this
same two-statistic design rather than a single "the score" mental model —
onboarding a new metric type requires understanding why there are two numbers,
not one.

**Foreclosed.** This design does not attempt to detect *gradual* drift across
many runs (e.g., a slow decline never large enough to trip a single-run delta
check) — that is explicitly the nightly tier's baseline-drift reporting
responsibility (FR-019), not this ADR's.

## Addendum — Feature 009 (2026-09-27)

Floors and the baseline-delta check now apply **per `(metric, tenant)` in addition to the
aggregate** (`eval/gate.py::evaluate_strata`, T042, research R-09) — the two per-run statistics this
ADR already distinguishes (variance-adjusted floor, raw-mean delta) are unchanged in kind; they are
now computed once per tenant stratum and once for the aggregate, using the same formulas. A tenant
stratum with fewer cases than `MIN_CASES_PER_TENANT` for the run's tier (`pr=2, merge_queue=5,
nightly=5` — placeholders pending real traffic data) never reaches either check: it fails through
the existing coverage verdict instead (`StratumResult.insufficient_data`), which keeps "we don't
have enough data to judge this tenant" distinguishable in the report from "we judged this tenant and
it's worse" — the same separation-of-concerns instinct the same separation this ADR already applies
to floor-vs-delta, extended to a third axis.

Two new deterministic (non-judge) metrics, `intent_extraction_accuracy` and `dietary_recall`
(`eval/intent_scorer.py`, T053, research R-08), are added to the same floor/delta machinery.
`dietary_recall`'s floor is 1.0 (Principle IV: every stated restriction must survive to the model
call) but is **report-only** until a real-credential calibration run confirms it doesn't flake on
its own noise (`eval/runner.py::DIETARY_RECALL_BLOCKING`, T053a) — a 1.0 floor that fails from
measurement noise rather than a real regression trains a team to ignore the gate, which is worse
than not gating at all.
