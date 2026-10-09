"""T017/T042/T052 — the binary/statistical gate combinator and Run Verdict computation
(FR-007/007a/007b/007c/021a, ADR-042, ADR-044/054 addenda, `contracts/eval-report-schema.md`'s
verdict algorithm). "A broken evaluator makes every other metric decorative" (`docs/models.md`)
— this module IS the evaluator's correctness backbone.

The two statistical-gate statistics are kept structurally separate, per
ADR-042: the absolute-floor check uses the variance-adjusted score
(mean - 1sigma over 3 repeats); the baseline-delta check compares RAW MEANS
on both sides, with the regression margin — not either operand — carrying
the noise tolerance. Conflating them was a bug caught mid-plan; see ADR-042.

T042/research R-09 extends this to per-tenant gating: `evaluate_strata` evaluates the same two
checks per `(metric, tenant)` cell AND on the aggregate, so a strong tenant can no longer carry a
weak one (SC-016). A tenant stratum below `MIN_CASES_PER_TENANT[tier]` is never scored on quality
at all — it fails as a coverage gap (`insufficient_data=True`), distinct from a stratum that was
scored and found wanting (a genuine `METRIC_FAILURE`), which is what lets `verdict_reason` report
"not enough data" and "the guests we do measure are getting a worse product" as different findings.

T052/research R-10 adds `BASELINE_STALE`: a judge-PROMPT change (as opposed to `BASELINE_INVALID`'s
judge-MODEL change) invalidates every historical score outright, so it is a hard FAIL ranked ahead of
ordinary metric and coverage failures — see `verdict_reason`'s precedence comment.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from eval.models import (
    Baseline,
    EvaluationRun,
    JudgeModelRecord,
    MetricScore,
    RunVerdict,
    StratumResult,
)

if TYPE_CHECKING:
    from collections.abc import Collection

_LOWER_IS_BETTER = frozenset(
    {
        "cost", "latency", "unnecessary_step_rate",
        # Feature 010 (contracts/routing-eval-gate.md)
        "oos_passthrough_rate", "substring_trap_misroutes", "modify_book_starts", "llm_tier_share",
        # Feature 011 (contracts/browse-eval-gate.md)
        "browse_catalogue_misroute", "near_miss_browse_leak", "browse_llm_tier_share",
    }
)

MIN_CASES_PER_TENANT: dict[str, int] = {"pr": 2, "merge_queue": 5, "nightly": 5}
"""Placeholder minimums pending real traffic data (spec.md Assumptions) — a `(metric, tenant)`
stratum with fewer cases than its tier's minimum can never pass; it fails through the coverage
verdict instead (T042)."""

ALL_TENANT_IDS: tuple[str, ...] = ("MGV", "MHB")
"""The closed, two-tenant set (`prompts.tenant.TenantProfile`; AGENTS.md's two jurisdictions).
Hardcoded rather than imported to keep this module's only dependency on tenant identity a single,
reviewable line — `agents/dining/tests/test_tenant_isolation.py` guards the source of truth."""

_DUMMY_JUDGE = JudgeModelRecord(provider="", model_id="")


def _higher_is_better(metric_name: str) -> bool:
    return metric_name not in _LOWER_IS_BETTER


def evaluate_absolute_floor(score: MetricScore, *, floor: float) -> bool:
    """FR-007 condition (a). Uses the variance-adjusted score when >=2
    samples exist (merge-queue tier, FR-007a); falls back to the raw single
    value for the PR tier's un-repeated subset."""
    value = score.variance_adjusted if score.variance_adjusted is not None else score.mean
    if _higher_is_better(score.metric_name):
        return value >= floor
    return value <= floor


def evaluate_baseline_delta(
    score: MetricScore,
    *,
    baseline_mean: float,
    baseline_stdev: float,
    margin_pct: float,
    baseline_status: str,
) -> bool:
    """FR-007 condition (b) / FR-007b. Compares RAW means on both sides —
    never the variance-adjusted value (ADR-042). Returns True (no
    regression) unconditionally when `baseline_status != "ok"`
    (`BASELINE_INVALID`/`no_baseline` suspend this check, FR-007c) — the
    absolute-floor check is unaffected and keeps applying regardless."""
    if baseline_status != "ok":
        return True
    margin = max(margin_pct * abs(baseline_mean), 2 * baseline_stdev)
    delta = score.mean - baseline_mean
    if _higher_is_better(score.metric_name):
        return delta >= -margin
    return delta <= margin


def evaluate_statistical_metric(
    score: MetricScore,
    *,
    floor: float,
    baseline_mean: float | None,
    baseline_stdev: float,
    margin_pct: float,
    baseline_status: str,
) -> bool:
    """FR-007's full statistical-gate rule: fails if EITHER condition (a) or
    condition (b) fails."""
    floor_ok = evaluate_absolute_floor(score, floor=floor)
    if baseline_mean is None:
        delta_ok = True  # no_baseline — nothing to compare against yet
    else:
        delta_ok = evaluate_baseline_delta(
            score,
            baseline_mean=baseline_mean,
            baseline_stdev=baseline_stdev,
            margin_pct=margin_pct,
            baseline_status=baseline_status,
        )
    passed = floor_ok and delta_ok
    score.passed = passed
    return passed


def _stratum_passed(
    metric_name: str,
    value: float,
    *,
    floor: float,
    baseline_mean: float | None,
    baseline_stdev: float,
    margin_pct: float,
    baseline_status: str,
) -> bool:
    """Reuses `evaluate_absolute_floor`/`evaluate_baseline_delta` (and their `_higher_is_better`
    direction and baseline-suspension rules) on a single aggregated value — a single-element
    `raw_values` list has no variance-adjusted score, so the floor check is the plain mean, which
    is the right comparison across DIFFERENT cases/tenants (the variance-adjustment in
    `evaluate_absolute_floor` is for repeats of the SAME case, ADR-042)."""
    synthetic = MetricScore(
        metric_name=metric_name, domain="", conversation_id="", gate_type="statistical",
        raw_values=[value], judge_model=_DUMMY_JUDGE,
    )
    floor_ok = evaluate_absolute_floor(synthetic, floor=floor)
    if baseline_mean is None:
        delta_ok = True
    else:
        delta_ok = evaluate_baseline_delta(
            synthetic, baseline_mean=baseline_mean, baseline_stdev=baseline_stdev,
            margin_pct=margin_pct, baseline_status=baseline_status,
        )
    return floor_ok and delta_ok


def evaluate_strata(
    run: EvaluationRun,
    *,
    floors: dict[str, float],
    baseline: Baseline | None = None,
    margin_pct: float = 0.10,
    report_only: Collection[str] = frozenset(),
) -> None:
    """T042: evaluates every scored metric per `(metric, tenant)` AND on the aggregate, appending
    to `run.stratum_results` and (for tenant-tagged metrics) `run.coverage`. Idempotent — re-running
    on the same `run` replaces rather than duplicates its own prior output, so a caller may call it
    again after adding more scores without hand-filtering old rows.

    A metric in `report_only` (e.g. `dietary_recall` before its calibration, T053a) is still scored
    and recorded (`StratumResult.blocking=False`) but can never fail the run by itself — see
    `compute_verdict`."""
    run.stratum_results = []
    run.coverage = [row for row in run.coverage if not str(row.get("surface_id", "")).startswith("tenant_stratum:")]

    scores_by_metric: dict[str, list[MetricScore]] = {}
    for score in run.metric_scores:
        if score.gate_type != "statistical":
            continue
        scores_by_metric.setdefault(score.metric_name, []).append(score)

    min_cases = MIN_CASES_PER_TENANT.get(run.tier, MIN_CASES_PER_TENANT["pr"])

    for metric_name, scores in scores_by_metric.items():
        floor = floors.get(metric_name, 0.5)
        blocking = metric_name not in report_only
        baseline_mean = baseline.per_metric_mean.get(metric_name) if baseline is not None else None
        baseline_stdev = baseline.per_metric_stdev.get(metric_name, 0.0) if baseline is not None else 0.0

        agg_value = statistics.fmean(v for s in scores for v in s.raw_values)
        agg_passed = _stratum_passed(
            metric_name, agg_value, floor=floor, baseline_mean=baseline_mean,
            baseline_stdev=baseline_stdev, margin_pct=margin_pct, baseline_status=run.baseline_status,
        )
        run.stratum_results.append(
            StratumResult(metric_name=metric_name, tenant_id=None, value=agg_value, passed=agg_passed, blocking=blocking)
        )

        if not any(s.tenant_id is not None for s in scores):
            continue  # no tenant tagging on this metric — aggregate-only gating (back-compat)

        tenant_baseline_mean = baseline.per_tenant_mean.get(metric_name, {}) if baseline is not None else {}
        tenant_baseline_stdev = baseline.per_tenant_stdev.get(metric_name, {}) if baseline is not None else {}
        for tenant_id in ALL_TENANT_IDS:
            tenant_scores = [s for s in scores if s.tenant_id == tenant_id]
            covered = len(tenant_scores) >= min_cases
            run.coverage.append(
                {"surface_id": f"tenant_stratum:{metric_name}:{tenant_id}", "urgency_tier": "tier_1", "covered": covered}
            )
            if not tenant_scores:
                continue  # nothing to compute a value from; the coverage row above already fails this
            value = statistics.fmean(v for s in tenant_scores for v in s.raw_values)
            if covered:
                passed = _stratum_passed(
                    metric_name, value, floor=floor, baseline_mean=tenant_baseline_mean.get(tenant_id),
                    baseline_stdev=tenant_baseline_stdev.get(tenant_id, 0.0), margin_pct=margin_pct,
                    baseline_status=run.baseline_status,
                )
                insufficient = False
            else:
                # Below the per-tenant minimum: "never a pass" (T042), but this is a COVERAGE gap,
                # not a quality finding — excluded from METRIC_FAILURE below so the two stay
                # distinguishable in `verdict_reason`.
                passed, insufficient = False, True
            run.stratum_results.append(
                StratumResult(
                    metric_name=metric_name, tenant_id=tenant_id, value=value, passed=passed,
                    blocking=blocking, insufficient_data=insufficient,
                )
            )


def _blocking_stratum_failure(run: EvaluationRun) -> bool:
    return any(s.passed is False and s.blocking and not s.insufficient_data for s in run.stratum_results)


def compute_verdict(run: EvaluationRun) -> RunVerdict:
    """`contracts/eval-report-schema.md`'s verdict algorithm. Step order matters: INCOMPLETE
    overrides everything (FR-022); binary gates are zero-tolerance; `BASELINE_STALE` (T052,
    research R-10) is a hard FAIL ranked ahead of ordinary metric/coverage findings, because a
    stale judge prompt means every score in this run was measured by a prompt that no longer
    exists; statistical-gate failures (legacy `MetricScore.passed` and the per-stratum results of
    `evaluate_strata`) and uncovered coverage requirements both fail the merge but never mask an
    INCOMPLETE, a binary failure or staleness that already occurred."""
    if run.not_evaluated_cases:
        return "INCOMPLETE"
    if any(not case.passed for case in run.saga_case_results):
        return "FAIL"
    if run.judge_prompt_status == "BASELINE_STALE":
        return "FAIL"
    if any(score.passed is False for score in run.metric_scores):
        return "FAIL"
    if _blocking_stratum_failure(run):
        return "FAIL"
    if any(not row["covered"] for row in run.coverage):
        return "FAIL"
    return "PASS"


def verdict_reason(run: EvaluationRun) -> str | None:
    """Names WHICH check produced `compute_verdict`'s FAIL/INCOMPLETE, in the same precedence
    order, for the report and CI output (e.g. the `BASELINE_STALE` hint in `eval/runner.py`).
    `None` on PASS."""
    if run.not_evaluated_cases:
        return "INCOMPLETE"
    if any(not case.passed for case in run.saga_case_results):
        return "SAGA_FAILURE"
    if run.judge_prompt_status == "BASELINE_STALE":
        return "BASELINE_STALE"
    if any(score.passed is False for score in run.metric_scores):
        return "METRIC_FAILURE"
    if _blocking_stratum_failure(run):
        return "METRIC_FAILURE"
    if any(not row["covered"] for row in run.coverage):
        return "COVERAGE"
    return None
