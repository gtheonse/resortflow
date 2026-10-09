"""T030 — unit tests for `eval/gate.py`'s verdict-computation logic,
exercising every branch of `contracts/eval-report-schema.md`'s verdict
algorithm with synthetic scores."""

from __future__ import annotations

from eval.gate import (
    compute_verdict,
    evaluate_absolute_floor,
    evaluate_baseline_delta,
    evaluate_statistical_metric,
)
from eval.models import EvaluationRun, JudgeModelRecord, MetricScore, SagaCaseResult

_JUDGE = JudgeModelRecord(provider="azure-foundry", model_id="gpt-5-mini")


def _run(**kwargs) -> EvaluationRun:
    defaults = {"run_id": "r1", "tier": "merge_queue", "code_ref": "abc123", "judge_model": _JUDGE}
    defaults.update(kwargs)
    return EvaluationRun(**defaults)


def test_clean_pass():
    run = _run(
        metric_scores=[
            MetricScore(
                metric_name="faithfulness", domain="dining", conversation_id="c1",
                gate_type="statistical", raw_values=[0.9, 0.91, 0.89], judge_model=_JUDGE,
                passed=True,
            )
        ],
        saga_case_results=[SagaCaseResult(case_id="s1", passed=True)],
        coverage=[{"surface_id": "x", "urgency_tier": "tier_1", "covered": True}],
    )
    assert compute_verdict(run) == "PASS"


def test_incomplete_overrides_everything():
    run = _run(not_evaluated_cases=["case-1"])
    assert compute_verdict(run) == "INCOMPLETE"


def test_binary_saga_failure_fails_the_gate():
    run = _run(saga_case_results=[SagaCaseResult(case_id="s1", passed=False)])
    assert compute_verdict(run) == "FAIL"


def test_uncovered_coverage_requirement_fails_the_gate():
    run = _run(coverage=[{"surface_id": "x", "urgency_tier": "tier_1", "covered": False}])
    assert compute_verdict(run) == "FAIL"


def test_absolute_floor_failure():
    score = MetricScore(
        metric_name="faithfulness", domain="dining", conversation_id="c1",
        gate_type="statistical", raw_values=[0.5, 0.5, 0.5], judge_model=_JUDGE,
    )
    assert evaluate_absolute_floor(score, floor=0.85) is False


def test_absolute_floor_pass_uses_variance_adjusted_not_raw_mean():
    # mean = 0.90, stdev > 0 => variance_adjusted < 0.90; floor set just
    # above variance_adjusted to prove the adjusted value (not raw mean) is
    # what gets checked.
    score = MetricScore(
        metric_name="faithfulness", domain="dining", conversation_id="c1",
        gate_type="statistical", raw_values=[0.80, 0.90, 1.00], judge_model=_JUDGE,
    )
    assert score.mean == 0.90
    # stdev of [0.80, 0.90, 1.00] is 0.1 -> variance_adjusted = 0.80
    assert round(score.variance_adjusted, 6) == 0.80
    assert evaluate_absolute_floor(score, floor=0.85) is False  # 0.80 < 0.85
    assert evaluate_absolute_floor(score, floor=0.79) is True


def test_baseline_delta_regression_detected_via_raw_means():
    score = MetricScore(
        metric_name="faithfulness", domain="dining", conversation_id="c1",
        gate_type="statistical", raw_values=[0.86, 0.86, 0.86], judge_model=_JUDGE,
    )
    # baseline 0.92, current 0.86 -> drop of 0.06; margin = max(1%*0.92, 2*0.01) = 0.02
    ok = evaluate_baseline_delta(
        score, baseline_mean=0.92, baseline_stdev=0.01, margin_pct=0.01, baseline_status="ok"
    )
    assert ok is False


def test_baseline_delta_within_margin_passes():
    score = MetricScore(
        metric_name="faithfulness", domain="dining", conversation_id="c1",
        gate_type="statistical", raw_values=[0.91, 0.91, 0.91], judge_model=_JUDGE,
    )
    ok = evaluate_baseline_delta(
        score, baseline_mean=0.92, baseline_stdev=0.01, margin_pct=0.05, baseline_status="ok"
    )
    assert ok is True


def test_baseline_invalid_suspends_delta_check_only():
    score = MetricScore(
        metric_name="faithfulness", domain="dining", conversation_id="c1",
        gate_type="statistical", raw_values=[0.5, 0.5, 0.5], judge_model=_JUDGE,
    )
    # Delta would otherwise fail hard, but BASELINE_INVALID suspends it —
    # the floor check still applies and still fails on its own.
    delta_ok = evaluate_baseline_delta(
        score, baseline_mean=0.99, baseline_stdev=0.01, margin_pct=0.01,
        baseline_status="BASELINE_INVALID",
    )
    assert delta_ok is True  # suspended, not a false regression
    floor_ok = evaluate_absolute_floor(score, floor=0.85)
    assert floor_ok is False  # unaffected by BASELINE_INVALID


def test_evaluate_statistical_metric_sets_passed_on_score():
    score = MetricScore(
        metric_name="faithfulness", domain="dining", conversation_id="c1",
        gate_type="statistical", raw_values=[0.9, 0.9, 0.9], judge_model=_JUDGE,
    )
    result = evaluate_statistical_metric(
        score, floor=0.85, baseline_mean=0.90, baseline_stdev=0.01,
        margin_pct=0.05, baseline_status="ok",
    )
    assert result is True
    assert score.passed is True


def test_lower_is_better_metric_direction():
    # latency: lower is better. Floor is a MAXIMUM acceptable value here.
    score = MetricScore(
        metric_name="latency", domain="dining", conversation_id="c1",
        gate_type="statistical", raw_values=[500.0, 500.0, 500.0], judge_model=_JUDGE,
    )
    assert evaluate_absolute_floor(score, floor=1000.0) is True  # 500 <= 1000, good
    assert evaluate_absolute_floor(score, floor=100.0) is False  # 500 > 100, bad
