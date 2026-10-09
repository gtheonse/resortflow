"""T044 -- every judge-scored metric in a written report carries its prompt identity next to the
provider and model (FR-017), so a report tells you not just "which model judged this" but "which
EXACT prompt version it was shown" -- the thing `BASELINE_STALE` (T041) keys off of.

Also checks the (deliberately trivial) back-compat case: a report written before this feature added
the three `prompt_*` fields still loads -- `JudgeModelRecord`'s new fields all default to `None`, so
an old dict missing them constructs cleanly.
"""

from __future__ import annotations

from dataclasses import asdict

from eval.models import EvaluationRun, JudgeModelRecord, MetricScore
from eval.report import assemble_report

_JUDGE = JudgeModelRecord(provider="azure-foundry", model_id="gpt-5-mini")


def test_a_judge_scored_metric_carries_prompt_id_sha256_and_version_in_the_report() -> None:
    judge_with_prompt = JudgeModelRecord(
        provider="azure-foundry", model_id="gpt-5-mini",
        prompt_id="eval_retrieval_judge_faithfulness", prompt_sha256="a" * 64, prompt_version="git-aaaaaaaaaaaa",
    )
    score = MetricScore(
        metric_name="faithfulness", domain="dining", conversation_id="c1", gate_type="statistical",
        raw_values=[0.9], judge_model=judge_with_prompt,
    )
    run = EvaluationRun(run_id="r1", tier="pr", code_ref="abc", judge_model=_JUDGE, metric_scores=[score])
    report = assemble_report(run)
    (row,) = report["metric_scores"]
    assert row["judge_model"]["prompt_id"] == "eval_retrieval_judge_faithfulness"
    assert row["judge_model"]["prompt_sha256"] == "a" * 64
    assert row["judge_model"]["prompt_version"] == "git-aaaaaaaaaaaa"


def test_a_deterministic_metric_carries_no_prompt_at_all() -> None:
    score = MetricScore(
        metric_name="intent_extraction_accuracy", domain="dining", conversation_id="c1", gate_type="statistical",
        raw_values=[1.0], judge_model=JudgeModelRecord(provider="", model_id=""),
    )
    run = EvaluationRun(run_id="r1", tier="pr", code_ref="abc", judge_model=_JUDGE, metric_scores=[score])
    report = assemble_report(run)
    (row,) = report["metric_scores"]
    assert row["judge_model"]["prompt_id"] is None
    assert row["judge_model"]["prompt_sha256"] is None


def test_a_report_written_before_this_feature_still_loads() -> None:
    """Simulates an old-shape dict (no prompt_* keys) being reconstructed into `JudgeModelRecord`
    — every new field is optional with a `None` default, so `**old_dict` still works."""
    old_dict = {"provider": "azure-foundry", "model_id": "gpt-4.1"}
    record = JudgeModelRecord(**old_dict)
    assert record.prompt_id is None
    assert record.prompt_sha256 is None
    assert record.prompt_version is None
    # And it still round-trips through asdict/report assembly without error.
    score = MetricScore(
        metric_name="faithfulness", domain="dining", conversation_id="c1", gate_type="statistical",
        raw_values=[0.8], judge_model=record,
    )
    run = EvaluationRun(run_id="r1", tier="pr", code_ref="abc", judge_model=_JUDGE, metric_scores=[score])
    report = assemble_report(run)
    assert asdict(record)["prompt_id"] is None
    assert report["metric_scores"][0]["judge_model"]["prompt_id"] is None
