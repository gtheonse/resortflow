"""T033 — smoke test wiring `eval/saga_suite.py`'s `run_saga_suite` output
directly into an `EvaluationRun` and confirming `eval/gate.py`'s
`compute_verdict` treats it as the zero-tolerance binary gate FR-007
requires (T034): any single failing `SagaCaseResult` fails the whole run,
regardless of every other score."""

from __future__ import annotations

from eval.gate import compute_verdict
from eval.models import EvaluationRun, JudgeModelRecord, SagaCaseResult
from eval.saga_suite import run_saga_suite


def _new_run(**overrides) -> EvaluationRun:
    defaults = {
        "run_id": "saga-integration-1",
        "tier": "pr",
        "code_ref": "deadbeef",
        "judge_model": JudgeModelRecord(provider="azure", model_id="gpt-4.1-mini"),
    }
    defaults.update(overrides)
    return EvaluationRun(**defaults)


def test_run_saga_suite_result_type_is_ready_for_direct_assignment():
    run = _new_run()
    run.saga_case_results = run_saga_suite(domains=["dining"])
    assert all(isinstance(r, SagaCaseResult) for r in run.saga_case_results)


def test_gate_fails_when_a_saga_case_is_injected_as_failing():
    run = _new_run()
    results = run_saga_suite(domains=["dining"])
    # Inject one deliberate failure — a real regression must fail the gate
    # outright, independent of every other passing case (zero tolerance).
    results.append(SagaCaseResult(case_id="injected.failure", passed=False, domain="dining", failing_step="hold"))
    run.saga_case_results = results

    assert compute_verdict(run) == "FAIL"


def test_gate_does_not_fail_from_saga_when_all_injected_cases_pass():
    run = _new_run()
    run.saga_case_results = [
        SagaCaseResult(case_id="synthetic.ok", passed=True, domain="dining", failing_step="none"),
    ]

    # No saga failure, no metric scores, no coverage gaps, nothing
    # unevaluated — the gate has nothing left to fail on.
    assert compute_verdict(run) != "FAIL"
