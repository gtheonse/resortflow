"""T053 -- deterministic dining-intent metrics (research R-08, FR-035): today's gate scores
retrieval, trajectory and saga behaviour but never the dining intent prompt, so a degraded dining
prompt passes every existing check. This module closes that gap with two metrics that need no
judge model at all ("never put an LLM where arithmetic will do") -- they run `AzureIntentExtractor`
through `prompts.registry` (so the resolved prompt's identity is exactly what production would send)
and compare its STRUCTURED output against a labelled case.

* `intent_extraction_accuracy` -- fraction of {cuisine, party_size, date_str, time_str} that match
  the case's expected value exactly.
* `dietary_recall` -- fraction of the case's STATED dietary requirements that appear in the
  extracted payload with the same restriction AND severity (a wrong severity is a miss, not a
  partial credit) (Principle IV: every stated restriction must survive to the model call). Floor
  1.0 -- report-only until `T053a` calibrates it (`eval/runner.py::DIETARY_RECALL_BLOCKING`).

Both are tenant-stratified (FR-021a) via `MetricScore.tenant_id`. No judge model is called, so
there is no retry/`INCOMPLETE` handling here -- `IntentExtractorUnavailableError` marks the one way
this module cannot produce a score at all (no Azure credentials), for the caller to turn into
`not_evaluated_cases`, mirroring `eval/judge.py`'s `JudgeUnavailableError` philosophy: never a
fabricated score.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

from agents.dining.nodes.intent_parser import AzureIntentExtractor, IntentExtractor
from eval.models import JudgeModelRecord, MetricScore

_FIELDS = ("cuisine", "party_size", "date_str", "time_str")


class IntentExtractorUnavailableError(RuntimeError):
    """No Azure OpenAI credentials are configured — never fabricate a score (mirrors
    `eval/judge.py::JudgeUnavailableError`)."""


def _judge_model_record() -> JudgeModelRecord:
    """Not a judge call at all (`gate_type="statistical"`, no model score) — a `JudgeModelRecord`
    with an empty provider/model_id keeps `MetricScore`'s shape uniform without implying a judge
    was involved (`eval/report.py` serialises it as-is)."""
    return JudgeModelRecord(provider="", model_id="")


def default_intent_extractor() -> IntentExtractor:
    """`AzureIntentExtractor` when credentials exist; raises otherwise rather than silently falling
    back to `RuleBasedIntentExtractor` (`agents.dining.nodes.intent_parser.build_default_extractor`'s
    fallback would make a degraded prompt invisible to this exact gate, defeating FR-035)."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    if not (endpoint and api_key):
        raise IntentExtractorUnavailableError(
            "no Azure OpenAI credentials configured — set AZURE_OPENAI_ENDPOINT+AZURE_OPENAI_API_KEY"
        )
    return AzureIntentExtractor(
        endpoint=endpoint,
        api_key=api_key,
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        deployment=os.environ.get("MODEL_CHEAP", "gpt-4.1-mini"),
    )


def _accuracy(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    matches = sum(1 for f in _FIELDS if actual.get(f) == expected.get(f))
    return matches / len(_FIELDS)


def _dietary_recall(expected: list[dict[str, str]], actual_dietary: list[Any]) -> float:
    if not expected:
        return 1.0  # nothing stated, nothing to miss
    found = [(d.restriction.value, d.severity.value) for d in actual_dietary]
    hits = sum(1 for e in expected if (e["restriction"], e["severity"]) in found)
    return hits / len(expected)


def score_intent_case(
    case: dict[str, Any], *, extractor: IntentExtractor, today: date
) -> list[MetricScore]:
    """Scores one `eval/data/intent_cases.yaml` case against `extractor`. Returns both metrics,
    tagged with the case's `tenant_id` (FR-021a)."""
    actual = extractor.extract(case["text"], today=today)
    expected = case["expected"]
    tenant_id = case.get("tenant_id")
    conversation_id = case["id"]
    judge_model = _judge_model_record()

    accuracy = _accuracy(expected, dict(actual))  # `actual` is a plain dict at runtime (TypedDict)
    recall = _dietary_recall(expected.get("dietary", []), actual.get("dietary", []))

    return [
        MetricScore(
            metric_name="intent_extraction_accuracy",
            domain="dining",
            conversation_id=conversation_id,
            gate_type="statistical",
            raw_values=[accuracy],
            judge_model=judge_model,
            tenant_id=tenant_id,
        ),
        MetricScore(
            metric_name="dietary_recall",
            domain="dining",
            conversation_id=conversation_id,
            gate_type="statistical",
            raw_values=[recall],
            judge_model=judge_model,
            tenant_id=tenant_id,
        ),
    ]


def score_intent_cases(
    cases: list[dict[str, Any]], *, extractor: IntentExtractor, today: date
) -> list[MetricScore]:
    """Convenience entrypoint for `eval/runner.py`: all cases, both metrics, flattened."""
    scores: list[MetricScore] = []
    for case in cases:
        scores.extend(score_intent_case(case, extractor=extractor, today=today))
    return scores
