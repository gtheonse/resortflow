"""T020/T049 — hand-rolled judge-prompt scorers for the four RAG quality metrics
(FR-001, ADR-043): context recall, context precision, faithfulness, and
answer relevance. Each metric is judged independently (ADR-043 rejects a
single composite "RAG score" prompt — a regression in one axis must stay
attributable, not averaged away) but all four share one call shape: resolve
the judge's Git-pinned prompt (`prompts.registry`, `resolution: exact` —
ADR-052/053, research R-06), call `eval.judge.JudgeClient.complete`, parse a
`{"score": float, "reasoning": str}` JSON object, fold the result into a
`MetricScore` that also carries the judge prompt's identity (FR-017).

The four prompts (`eval_retrieval_judge_context_recall/_context_precision/_faithfulness/
_answer_relevance`) are migrated verbatim from this module's original inline `_SYSTEM_PROMPT`
and per-metric f-strings — see `prompts/tests/test_judge_templates.py`'s golden fixture for the
byte-for-byte proof.

Malformed judge output is a genuine judge failure, not a scoring edge case —
`_parse_judge_json` lets `json.JSONDecodeError`/`KeyError` propagate rather
than substituting a fabricated 0.0 (mirrors `eval/judge.py`'s
`JudgeUnavailableError` philosophy: no path in this module invents a score).
`eval/runner.py`'s T026 retry/INCOMPLETE handling is the intended catcher.
"""

from __future__ import annotations

import json
from typing import Any

from eval.judge import JudgeClient, build_default_judge_client, pinned_model_id, provider_name
from eval.models import JudgeModelRecord, MetricScore
from prompts.registry import resolve

_PROMPT_IDS: dict[str, str] = {
    "context_recall": "eval_retrieval_judge_context_recall",
    "context_precision": "eval_retrieval_judge_context_precision",
    "faithfulness": "eval_retrieval_judge_faithfulness",
    "answer_relevance": "eval_retrieval_judge_answer_relevance",
}


def _judge_model_record(*, prompt_id: str, prompt_sha256: str, prompt_version: str) -> JudgeModelRecord:
    return JudgeModelRecord(
        provider=provider_name(), model_id=pinned_model_id(),
        prompt_id=prompt_id, prompt_sha256=prompt_sha256, prompt_version=prompt_version,
    )


def _parse_judge_json(raw: str) -> float:
    """Parses the judge's `{"score": ..., "reasoning": ...}` response.
    Deliberately does NOT catch/clamp malformed JSON or an out-of-range
    score — a judge that can't follow the response contract is a judge
    failure the caller (T026) should retry-then-INCOMPLETE, not a 0.0."""
    obj = json.loads(raw)
    score = float(obj["score"])
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"judge score out of range [0,1]: {score}")
    return score


def _score_with_judge(
    *,
    metric_name: str,
    domain: str,
    conversation_id: str,
    step_id: str | None,
    runtime_vars: dict[str, str],
    judge_client: JudgeClient,
    repeats: int = 1,
) -> MetricScore:
    resolved = resolve(_PROMPT_IDS[metric_name], required_vars={}, runtime_vars=runtime_vars)
    system, user = resolved.messages
    raw_values = [
        _parse_judge_json(judge_client.complete(system=system["content"], user=user["content"], prompt=resolved))
        for _ in range(repeats)
    ]
    return MetricScore(
        metric_name=metric_name,
        domain=domain,
        conversation_id=conversation_id,
        gate_type="statistical",
        raw_values=raw_values,
        judge_model=_judge_model_record(
            prompt_id=resolved.id, prompt_sha256=resolved.sha256, prompt_version=resolved.version
        ),
        step_id=step_id,
    )


def _bullets(items: list[str], *, empty: str) -> str:
    return "\n".join(f"- {x}" for x in items) or empty


def score_context_recall(
    *,
    question: str,
    retrieved_context: list[str],
    ground_truth_facts: list[str],
    domain: str,
    conversation_id: str,
    step_id: str | None = None,
    judge_client: JudgeClient | None = None,
    repeats: int = 1,
) -> MetricScore:
    """Of the facts a correct answer needed, what fraction were present
    somewhere in the retrieved context? (Recall — did retrieval miss
    anything relevant that existed in the corpus.)"""
    client = judge_client or build_default_judge_client()
    return _score_with_judge(
        metric_name="context_recall",
        domain=domain,
        conversation_id=conversation_id,
        step_id=step_id,
        runtime_vars={
            "question": question,
            "context_block": _bullets(retrieved_context, empty="(none retrieved)"),
            "facts_block": _bullets(ground_truth_facts, empty="(none specified)"),
        },
        judge_client=client,
        repeats=repeats,
    )


def score_context_precision(
    *,
    question: str,
    retrieved_context: list[str],
    domain: str,
    conversation_id: str,
    step_id: str | None = None,
    judge_client: JudgeClient | None = None,
    repeats: int = 1,
) -> MetricScore:
    """Of what was retrieved, how much was actually relevant to the
    question? (Precision — did retrieval drag in noise.)"""
    client = judge_client or build_default_judge_client()
    return _score_with_judge(
        metric_name="context_precision",
        domain=domain,
        conversation_id=conversation_id,
        step_id=step_id,
        runtime_vars={
            "question": question,
            "context_block": _bullets(retrieved_context, empty="(none retrieved)"),
        },
        judge_client=client,
        repeats=repeats,
    )


def score_faithfulness(
    *,
    answer: str,
    retrieved_context: list[str],
    domain: str,
    conversation_id: str,
    step_id: str | None = None,
    judge_client: JudgeClient | None = None,
    repeats: int = 1,
) -> MetricScore:
    """Is every claim in the answer actually supported by the retrieved
    context — i.e. is the model hallucinating beyond what it retrieved?"""
    client = judge_client or build_default_judge_client()
    return _score_with_judge(
        metric_name="faithfulness",
        domain=domain,
        conversation_id=conversation_id,
        step_id=step_id,
        runtime_vars={
            "answer": answer,
            "context_block": _bullets(retrieved_context, empty="(none retrieved)"),
        },
        judge_client=client,
        repeats=repeats,
    )


def score_answer_relevance(
    *,
    question: str,
    answer: str,
    domain: str,
    conversation_id: str,
    step_id: str | None = None,
    judge_client: JudgeClient | None = None,
    repeats: int = 1,
) -> MetricScore:
    """Does the answer actually address the guest's question, independent
    of whether it's grounded (that's faithfulness's job)?"""
    client = judge_client or build_default_judge_client()
    return _score_with_judge(
        metric_name="answer_relevance",
        domain=domain,
        conversation_id=conversation_id,
        step_id=step_id,
        runtime_vars={"question": question, "answer": answer},
        judge_client=client,
        repeats=repeats,
    )


def score_retrieval_case(
    case: dict[str, Any],
    *,
    judge_client: JudgeClient | None = None,
    repeats: int = 1,
) -> list[MetricScore]:
    """Convenience entrypoint for `eval/runner.py`'s PR/merge-queue wiring:
    scores all four metrics for one retrieval case dict of the shape
    `{id, domain, question, answer, retrieved_context: list[str],
    ground_truth_facts: list[str]}`. `repeats` > 1 is the merge-queue tier's
    3x-repeat scope (T025) — each metric's raw judge calls, not a wrapping
    average, land in that `MetricScore.raw_values`."""
    client = judge_client or build_default_judge_client()
    domain = case.get("domain", "")
    conversation_id = case.get("id", "")
    retrieved_context = case.get("retrieved_context", [])
    tenant_id = case.get("tenant_id")
    scores = [
        score_context_recall(
            question=case["question"],
            retrieved_context=retrieved_context,
            ground_truth_facts=case.get("ground_truth_facts", []),
            domain=domain,
            conversation_id=conversation_id,
            judge_client=client,
            repeats=repeats,
        ),
        score_context_precision(
            question=case["question"],
            retrieved_context=retrieved_context,
            domain=domain,
            conversation_id=conversation_id,
            judge_client=client,
            repeats=repeats,
        ),
        score_faithfulness(
            answer=case["answer"],
            retrieved_context=retrieved_context,
            domain=domain,
            conversation_id=conversation_id,
            judge_client=client,
            repeats=repeats,
        ),
        score_answer_relevance(
            question=case["question"],
            answer=case["answer"],
            domain=domain,
            conversation_id=conversation_id,
            judge_client=client,
            repeats=repeats,
        ),
    ]
    for score in scores:
        score.tenant_id = tenant_id
    return scores
