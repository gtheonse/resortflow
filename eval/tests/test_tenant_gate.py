"""T042 -- the gate is per tenant (FR-021a, SC-016, research R-09).

Floors and the baseline-delta check are evaluated per `(metric, tenant)` AND on the aggregate, so a strong tenant can
no longer carry a weak one: MGV at 1.0 and MHB at 0.5 average 0.9, which clears a 0.8 floor while MHB's guests get
a worse product. A tenant stratum with too few cases is a coverage failure, never a pass. Scores that carry no
`tenant_id` (the trajectory metrics, and anything written before this feature) are still gated on the aggregate only.
"""

from __future__ import annotations

from eval.gate import MIN_CASES_PER_TENANT, compute_verdict, evaluate_strata, verdict_reason
from eval.models import Baseline, EvaluationRun, JudgeModelRecord, MetricScore

_JUDGE = JudgeModelRecord(provider="azure-foundry", model_id="gpt-5-mini")
FLOORS = {"faithfulness": 0.8, "dietary_recall": 1.0}


def _scores(metric: str, tenant: str | None, values: list[float]) -> list[MetricScore]:
    return [
        MetricScore(
            metric_name=metric,
            domain="dining",
            conversation_id=f"{tenant}-{i}",
            gate_type="statistical",
            raw_values=[v],
            judge_model=_JUDGE,
            tenant_id=tenant,
        )
        for i, v in enumerate(values)
    ]


def _run(scores: list[MetricScore], *, tier: str = "merge_queue", **kw) -> EvaluationRun:
    return EvaluationRun(
        run_id="r1", tier=tier, code_ref="abc", judge_model=_JUDGE, metric_scores=scores, **kw  # type: ignore[arg-type]
    )


def _gate(run: EvaluationRun, *, baseline: Baseline | None = None, report_only=frozenset()) -> None:
    evaluate_strata(run, floors=FLOORS, baseline=baseline, margin_pct=0.10, report_only=report_only)


def _stratum(run: EvaluationRun, metric: str, tenant: str | None):
    (found,) = [s for s in run.stratum_results if s.metric_name == metric and s.tenant_id == tenant]
    return found


def test_minimum_cases_are_the_documented_placeholders() -> None:
    assert MIN_CASES_PER_TENANT == {"pr": 2, "merge_queue": 5, "nightly": 5}


def test_aggregate_above_floor_with_one_tenant_below_its_floor_fails() -> None:
    run = _run(_scores("faithfulness", "MGV", [1.0] * 8) + _scores("faithfulness", "MHB", [0.5] * 5))
    _gate(run)
    aggregate = _stratum(run, "faithfulness", None)
    mhb = _stratum(run, "faithfulness", "MHB")
    assert aggregate.value > FLOORS["faithfulness"] and aggregate.passed is True
    assert mhb.value < FLOORS["faithfulness"] and mhb.passed is False
    assert _stratum(run, "faithfulness", "MGV").passed is True
    assert compute_verdict(run) == "FAIL"
    assert verdict_reason(run) == "METRIC_FAILURE"


def test_every_stratum_above_its_floor_passes() -> None:
    run = _run(_scores("faithfulness", "MGV", [0.9] * 5) + _scores("faithfulness", "MHB", [0.85] * 5))
    _gate(run)
    assert all(s.passed for s in run.stratum_results)
    assert compute_verdict(run) == "PASS"


def test_a_tenant_below_the_minimum_cases_is_a_coverage_failure_never_a_pass() -> None:
    run = _run(_scores("faithfulness", "MGV", [0.9] * 5) + _scores("faithfulness", "MHB", [0.9] * 4))
    _gate(run)
    (row,) = [r for r in run.coverage if not r["covered"]]
    assert row["surface_id"] == "tenant_stratum:faithfulness:MHB"
    assert compute_verdict(run) == "FAIL"
    assert verdict_reason(run) == "COVERAGE"


def test_pr_tier_needs_two_cases_per_tenant() -> None:
    thin = _run(_scores("faithfulness", "MGV", [0.9, 0.9]) + _scores("faithfulness", "MHB", [0.9]), tier="pr")
    _gate(thin)
    assert compute_verdict(thin) == "FAIL"
    enough = _run(_scores("faithfulness", "MGV", [0.9, 0.9]) + _scores("faithfulness", "MHB", [0.9, 0.9]), tier="pr")
    _gate(enough)
    assert compute_verdict(enough) == "PASS"


def test_a_tenant_with_no_cases_at_all_is_a_coverage_failure() -> None:
    run = _run(_scores("faithfulness", "MGV", [0.9] * 5), tier="merge_queue")
    _gate(run)
    assert "tenant_stratum:faithfulness:MHB" in [r["surface_id"] for r in run.coverage if not r["covered"]]
    assert compute_verdict(run) == "FAIL"


def test_the_baseline_delta_is_checked_per_tenant() -> None:
    baseline = Baseline(
        source_run_id="b0",
        judge_model=_JUDGE,
        per_metric_mean={"faithfulness": 0.9},
        per_metric_stdev={"faithfulness": 0.0},
        per_tenant_mean={"faithfulness": {"MGV": 0.9, "MHB": 0.9}},
        per_tenant_stdev={"faithfulness": {"MGV": 0.0, "MHB": 0.0}},
    )
    run = _run(_scores("faithfulness", "MGV", [0.95] * 5) + _scores("faithfulness", "MHB", [0.80] * 5))
    run.baseline_status = "ok"
    _gate(run, baseline=baseline)
    mhb = _stratum(run, "faithfulness", "MHB")
    assert mhb.value >= FLOORS["faithfulness"]  # the floor alone would pass...
    assert mhb.passed is False  # ...but MHB is 0.10 under its own baseline; the margin is 10% of 0.9 = 0.09
    assert _stratum(run, "faithfulness", "MGV").passed is True
    assert compute_verdict(run) == "FAIL"


def test_a_suspended_baseline_still_applies_the_floor() -> None:
    baseline = Baseline(
        source_run_id="b0",
        judge_model=_JUDGE,
        per_metric_mean={"faithfulness": 0.99},
        per_metric_stdev={"faithfulness": 0.0},
        per_tenant_mean={"faithfulness": {"MGV": 0.99, "MHB": 0.99}},
        per_tenant_stdev={"faithfulness": {"MGV": 0.0, "MHB": 0.0}},
    )
    ok = _run(_scores("faithfulness", "MGV", [0.85] * 5) + _scores("faithfulness", "MHB", [0.85] * 5))
    ok.baseline_status = "BASELINE_INVALID"
    _gate(ok, baseline=baseline)
    assert compute_verdict(ok) == "PASS"  # a 0.14 drop would fail the delta, but the delta is suspended

    low = _run(_scores("faithfulness", "MGV", [0.85] * 5) + _scores("faithfulness", "MHB", [0.5] * 5))
    low.baseline_status = "BASELINE_INVALID"
    _gate(low, baseline=baseline)
    assert compute_verdict(low) == "FAIL"


def test_scores_without_a_tenant_are_gated_on_the_aggregate_only() -> None:
    run = _run(_scores("faithfulness", None, [0.9, 0.95]))
    _gate(run)
    assert [(s.tenant_id, s.passed) for s in run.stratum_results] == [(None, True)]
    assert run.coverage == []
    assert compute_verdict(run) == "PASS"

    failing = _run(_scores("faithfulness", None, [0.4, 0.5]))
    _gate(failing)
    assert compute_verdict(failing) == "FAIL"


def test_report_only_metrics_are_reported_but_never_block() -> None:
    values = {"MGV": [1.0] * 5, "MHB": [1.0, 1.0, 1.0, 1.0, 0.5]}
    scores = _scores("dietary_recall", "MGV", values["MGV"]) + _scores("dietary_recall", "MHB", values["MHB"])

    blocking = _run(list(scores))
    _gate(blocking)
    assert _stratum(blocking, "dietary_recall", "MHB").passed is False
    assert compute_verdict(blocking) == "FAIL"

    report_only = _run(list(scores))
    _gate(report_only, report_only=frozenset({"dietary_recall"}))
    stratum = _stratum(report_only, "dietary_recall", "MHB")
    assert stratum.passed is False and stratum.blocking is False
    assert compute_verdict(report_only) == "PASS"


def test_evaluating_twice_does_not_duplicate_results() -> None:
    run = _run(_scores("faithfulness", "MGV", [0.9] * 5) + _scores("faithfulness", "MHB", [0.9] * 4))
    _gate(run)
    _gate(run)
    assert len([r for r in run.coverage if not r["covered"]]) == 1
    assert len(run.stratum_results) == 3
