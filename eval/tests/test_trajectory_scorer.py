"""T021 tests — deterministic sequence-diff scoring + the judge-backed
plan-quality path (FakeJudgeClient only)."""

from __future__ import annotations

from eval.conversation_record import Turn
from eval.judge import FakeJudgeClient
from eval.trajectory_scorer import (
    score_plan_quality,
    score_tool_selection,
    score_turn_trajectory,
    score_unnecessary_step_rate,
)


def _calls(*kinds: str) -> list[dict]:
    return [{"kind": k} for k in kinds]


def test_exact_match_scores_one() -> None:
    score = score_tool_selection(
        actual_tool_calls=_calls("hold", "confirm"),
        reference_kinds=["hold", "confirm"],
        domain="dining",
        conversation_id="c1",
    )
    assert score.raw_values == [1.0]


def test_partial_match_lcs_ratio() -> None:
    score = score_tool_selection(
        actual_tool_calls=_calls("hold", "extra", "confirm"),
        reference_kinds=["hold", "confirm"],
        domain="dining",
        conversation_id="c2",
    )
    # LCS(["hold","extra","confirm"], ["hold","confirm"]) = 2 -> ratio 1.0? no:
    # exact match fails since sequences differ, but LCS length is 2/2 = 1.0
    assert score.raw_values == [1.0]


def test_missing_step_partial_credit() -> None:
    score = score_tool_selection(
        actual_tool_calls=_calls("hold"),
        reference_kinds=["hold", "confirm"],
        domain="dining",
        conversation_id="c3",
    )
    assert score.raw_values == [0.5]


def test_no_reference_no_calls_is_perfect() -> None:
    score = score_tool_selection(
        actual_tool_calls=[], reference_kinds=[], domain="dining", conversation_id="c4"
    )
    assert score.raw_values == [1.0]


def test_no_reference_but_calls_made_is_zero() -> None:
    score = score_tool_selection(
        actual_tool_calls=_calls("hold"), reference_kinds=[], domain="dining", conversation_id="c5"
    )
    assert score.raw_values == [0.0]


def test_unnecessary_step_rate() -> None:
    score = score_unnecessary_step_rate(
        actual_tool_calls=_calls("hold", "extra", "confirm"),
        reference_kinds=["hold", "confirm"],
        domain="dining",
        conversation_id="c6",
    )
    assert score.raw_values == [1 / 3]
    assert score.metric_name == "unnecessary_step_rate"


def test_unnecessary_step_rate_empty_actual_is_zero() -> None:
    score = score_unnecessary_step_rate(
        actual_tool_calls=[], reference_kinds=["hold"], domain="dining", conversation_id="c7"
    )
    assert score.raw_values == [0.0]


def test_score_turn_trajectory_returns_both_metrics() -> None:
    turn = Turn(input="book a table", tool_calls=_calls("hold", "confirm"))
    scores = score_turn_trajectory(
        turn, reference_kinds=["hold", "confirm"], domain="dining", conversation_id="c8"
    )
    names = {s.metric_name for s in scores}
    assert names == {"tool_selection", "unnecessary_step_rate"}


def test_score_plan_quality_uses_judge() -> None:
    client = FakeJudgeClient('{"score": 0.8, "reasoning": "good enough"}')
    score = score_plan_quality(
        actual_tool_calls=_calls("hold", "confirm"),
        acceptable_reference_sequences=[[{"kind": "hold"}, {"kind": "confirm"}], [{"kind": "confirm"}]],
        task_description="book a table",
        domain="dining",
        conversation_id="c9",
        judge_client=client,
    )
    assert score.raw_values == [0.8]
    assert score.metric_name == "plan_quality"
    assert len(client.calls) == 1
