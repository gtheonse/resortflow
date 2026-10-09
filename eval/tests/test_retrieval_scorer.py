"""T020 tests — retrieval scorer prompts/parsing, using FakeJudgeClient
only (no live network call, per repo convention)."""

from __future__ import annotations

import json

import pytest

from eval.judge import FakeJudgeClient
from eval.retrieval_scorer import (
    score_answer_relevance,
    score_context_precision,
    score_context_recall,
    score_faithfulness,
    score_retrieval_case,
)


def test_score_context_recall_parses_fake_response() -> None:
    client = FakeJudgeClient('{"score": 0.75, "reasoning": "mostly there"}')
    score = score_context_recall(
        question="What's the dress code?",
        retrieved_context=["Smart casual after 6pm."],
        ground_truth_facts=["Smart casual after 6pm.", "No shorts in the steakhouse."],
        domain="dining",
        conversation_id="c1",
        judge_client=client,
    )
    assert score.raw_values == [0.75]
    assert score.metric_name == "context_recall"
    assert score.domain == "dining"
    assert len(client.calls) == 1


def test_score_context_precision_repeats_call_count() -> None:
    client = FakeJudgeClient('{"score": 1.0, "reasoning": "fine"}')
    score = score_context_precision(
        question="q",
        retrieved_context=["a", "b"],
        domain="stay",
        conversation_id="c2",
        judge_client=client,
        repeats=3,
    )
    assert len(score.raw_values) == 3
    assert len(client.calls) == 3


def test_score_faithfulness_and_answer_relevance() -> None:
    client = FakeJudgeClient('{"score": 0.5, "reasoning": "partial"}')
    faith = score_faithfulness(
        answer="a", retrieved_context=["ctx"], domain="wellness", conversation_id="c3", judge_client=client
    )
    rel = score_answer_relevance(
        question="q", answer="a", domain="wellness", conversation_id="c3", judge_client=client
    )
    assert faith.raw_values == [0.5]
    assert rel.raw_values == [0.5]


def test_malformed_judge_json_propagates() -> None:
    client = FakeJudgeClient("not json")
    with pytest.raises(json.JSONDecodeError):
        score_context_recall(
            question="q",
            retrieved_context=[],
            ground_truth_facts=[],
            domain="dining",
            conversation_id="c4",
            judge_client=client,
        )


def test_out_of_range_score_raises() -> None:
    client = FakeJudgeClient('{"score": 1.5, "reasoning": "bad"}')
    with pytest.raises(ValueError):
        score_context_recall(
            question="q",
            retrieved_context=[],
            ground_truth_facts=[],
            domain="dining",
            conversation_id="c5",
            judge_client=client,
        )


def test_score_retrieval_case_returns_four_metrics() -> None:
    client = FakeJudgeClient('{"score": 0.9, "reasoning": "good"}')
    case = {
        "id": "case-1",
        "domain": "entertainment",
        "question": "What time is the show?",
        "answer": "8pm.",
        "retrieved_context": ["Show starts 8pm."],
        "ground_truth_facts": ["Show starts 8pm."],
    }
    scores = score_retrieval_case(case, judge_client=client)
    names = {s.metric_name for s in scores}
    assert names == {"context_recall", "context_precision", "faithfulness", "answer_relevance"}
    for s in scores:
        assert s.conversation_id == "case-1"
        assert s.domain == "entertainment"
