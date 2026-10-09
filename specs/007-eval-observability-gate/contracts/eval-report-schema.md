# Contract: Consolidated Evaluation Report

**Produced by**: `python -m eval.runner --tier <pr|merge_queue|nightly>` (FR-006).
**Consumed by**: CI (pass/fail exit code), the diagnostic view (FR-011/012), and
future phases' dashboards. This is the stable interface between the gate logic
and everything that reads its result — treat field removal or renaming as a
breaking change per the constitution's Contract Stability standard.

## Top-level shape

```jsonc
{
  "run_id": "string",
  "tier": "pr | merge_queue | nightly",
  "code_ref": "string (git SHA)",
  "started_at": "ISO-8601",
  "completed_at": "ISO-8601",
  "verdict": "PASS | FAIL | INCOMPLETE",
  "judge_model": { "provider": "string", "model_id": "string", "tier": "frontier" },
  "metric_scores": [ /* Metric Score, see data-model.md */ ],
  "saga_case_results": [ { "case_id": "string", "passed": true } ],
  "budget_flags": [ { "scope": "string", "metric": "cost|latency", "actual": 0.0, "threshold": 0.0 } ],
  "active_exemptions": [ /* Coverage Exemption, see coverage-exemption-schema.md */ ],
  "not_evaluated_cases": [ "string" ],
  "coverage": [ { "surface_id": "string", "urgency_tier": "tier_1|tier_2|other", "covered": true } ],
  "baseline_status": "ok | BASELINE_INVALID | no_baseline",
  "drift_findings": [ { "metric_name": "string", "domain": "string", "oldest_mean": 0.0, "newest_mean": 0.0, "delta": 0.0, "run_count": 0 } ]
}
```

## Verdict computation (consumers MUST NOT re-derive this differently)

1. If any required case is `not_evaluated_cases` after FR-021's retries →
   `verdict = INCOMPLETE`. This overrides everything below.
2. Else if any binary-gate case failed → `verdict = FAIL`.
3. Else if any statistical-gate metric fails its absolute floor
   (`variance_adjusted < floor`) or its baseline delta
   (`baseline_status = "ok"` AND `mean` regressed beyond
   `max(fixed_pct, 2 * baseline.stdev)`) → `verdict = FAIL`.
4. Else if any Tier 1/Tier 2 Coverage Requirement is uncovered and unexempted →
   `verdict = FAIL`.
5. Else → `verdict = PASS`.

`baseline_status = "BASELINE_INVALID"` suspends step 3's delta check only for the
affected metric(s) — it never by itself produces `FAIL` (FR-007c).

`drift_findings` (nightly tier only, `eval/drift.py`, T043) is report-only and is
never read by the verdict algorithm above — a drift finding must never change a
`PASS`/`FAIL`/`INCOMPLETE` outcome (FR-019: the nightly tier never blocks a merge).

## Blocking policy (environment-specific, NOT part of this schema)

Whether `verdict = INCOMPLETE` blocks a merge is read from CI configuration
(FR-023), not from this report. The report always states the same `verdict`
regardless of environment (SC-006) — only the CI job's exit-code mapping differs
between local/PR-tier-warn-mode and merge-queue-tier-block-mode.

## PII masking (FR-018/SC-016)

Any conversation-derived text embedded anywhere in this report (a saga-case
failure description, a metric-score detail, a coverage-exemption reason) MUST
pass through `observability/redaction.py`'s masking before assembly — the
report is written by the same `eval/report.py` step that also assembles
`saga_case_results` and `metric_scores`, so masking happens once, at assembly
time, not per-consumer.

## Compatibility

- New optional fields MAY be added without a version bump.
- Removing or renaming a field, or changing `verdict`'s computation order above,
  requires an ADR per the constitution's Contract Stability standard.
