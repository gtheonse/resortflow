"""T041 -- `BASELINE_STALE`: a judge-prompt hash change is a hard gate failure (FR-016a..FR-021, research R-10).

`BASELINE_INVALID` (a judge MODEL mismatch) suspends the baseline-delta check and never fails a run by itself.
`BASELINE_STALE` (a judge PROMPT changed since the baseline was captured) is different: every recorded score was
produced by a prompt that no longer exists, so the run FAILS until the baseline is regenerated in the same PR.

Staleness compares only the prompt ids the run actually used. A judge template that no metric in the run touched
(for example `eval_trajectory_judge_plan_quality` in a retrieval-only run) changing does not stale anything.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eval import runner as runner_mod
from eval.baseline import (
    apply_judge_prompt_status,
    baseline_status_for,
    judge_prompt_status_for,
    stale_judge_prompts_for,
)
from eval.gate import (
    compute_verdict,
    evaluate_absolute_floor,
    evaluate_baseline_delta,
    verdict_reason,
)
from eval.models import Baseline, EvaluationRun, JudgeModelRecord, MetricScore, SagaCaseResult
from eval.report import assemble_report
from prompts import registry
from prompts.lock import LockState
from prompts.schema import TEMPLATES_DIR

FAITH = "eval_retrieval_judge_faithfulness"
PLAN = "eval_trajectory_judge_plan_quality"
H1 = "a" * 64
H2 = "b" * 64
H3 = "c" * 64
_MODEL = JudgeModelRecord(provider="azure-foundry", model_id="gpt-5-mini")


def _record(prompt_id: str | None, sha: str | None) -> JudgeModelRecord:
    return JudgeModelRecord(
        provider="azure-foundry",
        model_id="gpt-5-mini",
        prompt_id=prompt_id,
        prompt_sha256=sha,
        prompt_version=f"git-{sha[:12]}" if sha else None,
    )


def _score(metric: str, prompt_id: str | None, sha: str | None, **kw) -> MetricScore:
    return MetricScore(
        metric_name=metric,
        domain="dining",
        conversation_id="c1",
        gate_type="statistical",
        raw_values=[0.9],
        judge_model=_record(prompt_id, sha),
        **kw,
    )


def _run(*scores: MetricScore, **kw) -> EvaluationRun:
    return EvaluationRun(
        run_id="r1", tier="merge_queue", code_ref="abc", judge_model=_MODEL, metric_scores=list(scores), **kw
    )


def _baseline(hashes: dict[str, str], **kw) -> Baseline:
    return Baseline(source_run_id="b0", judge_model=_MODEL, judge_prompt_hashes=hashes, **kw)


EMPTY = LockState()


# --- status ------------------------------------------------------------------------------------------------------


def test_equal_hashes_are_ok_and_give_no_reason() -> None:
    run = _run(_score("faithfulness", FAITH, H1))
    assert judge_prompt_status_for(run, _baseline({FAITH: H1}), EMPTY) == "ok"
    apply_judge_prompt_status(run, _baseline({FAITH: H1}), EMPTY)
    assert compute_verdict(run) == "PASS"
    assert verdict_reason(run) is None
    assert run.stale_judge_prompts == []


def test_a_changed_hash_is_stale_and_names_the_prompt() -> None:
    run = _run(_score("faithfulness", FAITH, H2))
    baseline = _baseline({FAITH: H1})
    assert judge_prompt_status_for(run, baseline, EMPTY) == "BASELINE_STALE"
    assert stale_judge_prompts_for(run, baseline, EMPTY) == [FAITH]
    apply_judge_prompt_status(run, baseline, EMPTY)
    assert run.judge_prompt_status == "BASELINE_STALE"
    assert compute_verdict(run) == "FAIL"
    assert verdict_reason(run) == "BASELINE_STALE"
    report = assemble_report(run)
    assert report["verdict"] == "FAIL"
    assert report["judge_prompt_status"] == "BASELINE_STALE"
    assert report["stale_judge_prompts"] == [FAITH]


def test_a_one_character_edit_to_a_real_judge_template_makes_the_baseline_stale(tmp_path: Path) -> None:
    """End to end through `resolve`: the hash the scorer records is the hash of the template text, so touching a
    single character of the template changes it (research R-05)."""
    variables = {"answer": "yes", "context_block": "- a"}
    registry.reset_caches()
    original = registry.resolve(FAITH, required_vars={}, runtime_vars=variables)

    copy = tmp_path / "templates"
    shutil.copytree(TEMPLATES_DIR, copy)
    target = copy / f"{FAITH}.yaml"
    text = target.read_text(encoding="utf-8")
    assert "strict" in text
    target.write_text(text.replace("strict", "Strict", 1), encoding="utf-8")
    registry.reset_caches()
    edited = registry.resolve(FAITH, required_vars={}, runtime_vars=variables, templates_dir=copy)
    registry.reset_caches()

    assert original.sha256 != edited.sha256
    run = _run(_score("faithfulness", FAITH, edited.sha256))
    assert judge_prompt_status_for(run, _baseline({FAITH: original.sha256}), EMPTY) == "BASELINE_STALE"


def test_a_recorded_hash_that_is_revoked_is_stale_even_when_it_matches() -> None:
    run = _run(_score("faithfulness", FAITH, H1))
    revoked = LockState(revoked={FAITH: frozenset({H1})})
    assert judge_prompt_status_for(run, _baseline({FAITH: H1}), revoked) == "BASELINE_STALE"


def test_with_no_baseline_the_status_is_absent_and_not_treated_as_passed() -> None:
    run = _run(_score("faithfulness", FAITH, H1))
    assert judge_prompt_status_for(run, None, EMPTY) == "absent"
    apply_judge_prompt_status(run, None, EMPTY)
    report = assemble_report(run)
    assert report["judge_prompt_status"] == "absent"
    assert report["baseline_status"] == "no_baseline"
    assert report["judge_prompt_status"] != "ok"


def test_a_baseline_written_before_this_feature_is_absent_not_ok() -> None:
    """A baseline with no recorded judge-prompt hashes cannot vouch for anything."""
    run = _run(_score("faithfulness", FAITH, H1))
    assert judge_prompt_status_for(run, _baseline({}), EMPTY) == "absent"


def test_a_prompt_no_metric_in_the_run_used_never_stales_the_baseline() -> None:
    run = _run(_score("faithfulness", FAITH, H1))
    baseline = _baseline({FAITH: H1, PLAN: H3})  # PLAN's current hash is irrelevant: this run did not use it
    assert judge_prompt_status_for(run, baseline, EMPTY) == "ok"


def test_a_used_prompt_the_baseline_never_recorded_is_stale() -> None:
    run = _run(_score("faithfulness", FAITH, H1), _score("plan_quality", PLAN, H2))
    assert stale_judge_prompts_for(run, _baseline({FAITH: H1}), EMPTY) == [PLAN]


def test_deterministic_scores_without_a_prompt_are_ignored() -> None:
    run = _run(_score("tool_selection", None, None), _score("faithfulness", FAITH, H1))
    assert judge_prompt_status_for(run, _baseline({FAITH: H1}), EMPTY) == "ok"


# --- verdict precedence: INCOMPLETE -> saga -> BASELINE_STALE -> metrics -> coverage -----------------------------


def _stale_run(**kw) -> EvaluationRun:
    run = _run(_score("faithfulness", FAITH, H2), **kw)
    apply_judge_prompt_status(run, _baseline({FAITH: H1}), EMPTY)
    return run


def test_incomplete_outranks_stale() -> None:
    run = _stale_run(not_evaluated_cases=["case-1"])
    assert compute_verdict(run) == "INCOMPLETE"
    assert verdict_reason(run) == "INCOMPLETE"


def test_a_saga_failure_outranks_stale_in_the_reason() -> None:
    run = _stale_run(saga_case_results=[SagaCaseResult(case_id="s1", passed=False)])
    assert compute_verdict(run) == "FAIL"
    assert verdict_reason(run) == "SAGA_FAILURE"


def test_stale_outranks_metric_and_coverage_failures_in_the_reason() -> None:
    run = _stale_run(coverage=[{"surface_id": "x", "urgency_tier": "tier_1", "covered": False}])
    run.metric_scores[0].passed = False
    assert compute_verdict(run) == "FAIL"
    assert verdict_reason(run) == "BASELINE_STALE"


def test_metric_failure_is_reported_when_nothing_is_stale() -> None:
    run = _run(_score("faithfulness", FAITH, H1))
    run.metric_scores[0].passed = False
    assert compute_verdict(run) == "FAIL"
    assert verdict_reason(run) == "METRIC_FAILURE"


def test_coverage_failure_is_reported_last() -> None:
    run = _run(coverage=[{"surface_id": "x", "urgency_tier": "tier_1", "covered": False}])
    assert verdict_reason(run) == "COVERAGE"


# --- BASELINE_INVALID is unchanged -------------------------------------------------------------------------------


def test_a_judge_model_mismatch_is_still_baseline_invalid_and_never_stale() -> None:
    other = JudgeModelRecord(provider="azure-foundry", model_id="a-different-model")
    baseline = _baseline({FAITH: H1})
    assert baseline_status_for(other, baseline) == "BASELINE_INVALID"
    run = _run(_score("faithfulness", FAITH, H1))
    assert judge_prompt_status_for(run, baseline, EMPTY) == "ok"


def test_baseline_invalid_still_suspends_delta_but_keeps_the_floor() -> None:
    score = _score("faithfulness", FAITH, H1)
    assert evaluate_baseline_delta(
        score, baseline_mean=0.99, baseline_stdev=0.0, margin_pct=0.1, baseline_status="BASELINE_INVALID"
    )
    assert not evaluate_absolute_floor(_score("faithfulness", FAITH, H1), floor=0.95)


# --- the CLI: exit code and the hint ----------------------------------------------------------------------------


def test_the_runner_exits_1_and_prints_the_rebaseline_hint_on_a_stale_baseline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _stale_run()
    monkeypatch.setattr(runner_mod, "run", lambda tier: run)
    monkeypatch.setattr(runner_mod, "write_report", lambda r: (assemble_report(r), Path("report.json"))[1])
    monkeypatch.setattr(runner_mod, "_maybe_update_baseline", lambda r: None)
    assert runner_mod.main(["--tier", "pr"]) == 1
    err = capsys.readouterr().err
    assert "BASELINE_STALE" in err
    assert FAITH in err
    assert "rebaseline" in err
