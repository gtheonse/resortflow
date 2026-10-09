"""T043a -- a degraded dining prompt fails the gate on `dietary_recall` (FR-035, research R-08).

`intent_extraction_accuracy`/`dietary_recall` exist precisely because the existing gate never
exercises the dining intent prompt: a model call is only as good as the instructions it was given,
so this test proves the metric actually reacts to a WEAKENED prompt, not just to a broken model.

A stub model client stands in for the real one: it returns the correct structured payload when the
system prompt it was given still instructs the model to extract dietary restrictions/severities
(the code-injected vocabulary, `agents.dining.nodes.intent_parser.dining_intent_required_vars`), and
silently drops the dietary items when that instruction is missing -- exactly what a real model is
liable to do when nobody asked it to look for allergens. This models the FAILURE MODE (a prompt edit
that stops asking for dietary data), not a broken JSON parser.
"""

from __future__ import annotations

from datetime import date

from agents.dining.nodes.intent_parser import dining_intent_required_vars
from eval.gate import compute_verdict, evaluate_strata
from eval.intent_scorer import score_intent_cases
from eval.models import EvaluationRun, JudgeModelRecord
from graph.models import DietaryRequirement, DietaryRestriction, DietarySeverity
from prompts import registry

TODAY = date(2026, 1, 15)
_JUDGE = JudgeModelRecord(provider="azure-foundry", model_id="gpt-5-mini")


def _current_system_prompt() -> str:
    registry.reset_caches()
    resolved = registry.resolve(
        "dining_intent_extraction",
        required_vars=dining_intent_required_vars(),
        runtime_vars={"guest_message": "placeholder"},
    )
    registry.reset_caches()
    return resolved.messages[0]["content"]


def _degrade(system_prompt: str) -> str:
    """Strips the clause that asks the model for dietary data -- the failure mode this test
    guards against, not a hypothetical: an editor tightening the schema description and dropping
    one field is exactly the kind of "small, plausible" prompt edit FR-035 exists for."""
    marker = ", dietary (array of"
    assert marker in system_prompt, "the current template no longer matches this test's assumption"
    return system_prompt.split(marker)[0] + ")."


class _StubModelExtractor:
    """Mirrors `AzureIntentExtractor.extract`'s post-processing (parses the same JSON payload into
    `IntentParsed`), but the "model" is a deterministic function of the system prompt it was given,
    not a network call."""

    def __init__(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt

    def extract(self, text: str, *, today: date) -> dict:
        if ", dietary (array of" in self._system_prompt:
            payload = {
                "cuisine": "steakhouse", "party_size": 2, "date_str": "2026-01-16", "time_str": "19:00",
                "occasion": None,
                "dietary": [{"restriction": "peanut", "severity": "allergy_severe", "detail": "peanut"}],
            }
        else:
            # The prompt never asked for it — a real model omits the field it was never told about.
            payload = {
                "cuisine": "steakhouse", "party_size": 2, "date_str": "2026-01-16", "time_str": "19:00",
                "occasion": None, "dietary": [],
            }
        dietary = [
            DietaryRequirement(
                restriction=DietaryRestriction(d["restriction"]),
                severity=DietarySeverity(d["severity"]),
                detail=d.get("detail"),
            )
            for d in payload["dietary"]
        ]
        return {**payload, "dietary": dietary, "ambiguous_fields": []}


_EXPECTED = {
    "cuisine": "steakhouse", "party_size": 2, "date_str": "2026-01-16", "time_str": "19:00",
    "dietary": [{"restriction": "peanut", "severity": "allergy_severe"}],
}
CASES = [
    # Two cases per tenant — `evaluate_strata`'s per-tenant coverage minimum for the PR tier is 2
    # (`MIN_CASES_PER_TENANT["pr"]`); fewer, or a tenant with none at all, would fail on COVERAGE
    # before the metric floor ever gets a say (that's T042's test, not this one), and
    # `evaluate_strata` always checks both tenants in the closed set (`ALL_TENANT_IDS`).
    {
        "id": "c1", "tenant_id": "MGV",
        "text": "table for 2 tomorrow at 7pm, steakhouse, severe peanut allergy",
        "expected": _EXPECTED,
    },
    {
        "id": "c2", "tenant_id": "MGV",
        "text": "party of 2 tomorrow, 7pm, steakhouse please, severe peanut allergy",
        "expected": _EXPECTED,
    },
    {
        "id": "c3", "tenant_id": "MHB",
        "text": "table for 2 tomorrow at 7pm, steakhouse, severe peanut allergy",
        "expected": _EXPECTED,
    },
    {
        "id": "c4", "tenant_id": "MHB",
        "text": "party of 2 tomorrow, 7pm, steakhouse please, severe peanut allergy",
        "expected": _EXPECTED,
    },
]


def _run(extractor) -> EvaluationRun:
    scores = score_intent_cases(CASES, extractor=extractor, today=TODAY)
    return EvaluationRun(run_id="r1", tier="pr", code_ref="abc", judge_model=_JUDGE, metric_scores=scores)


def test_the_current_template_yields_a_perfect_recall() -> None:
    run = _run(_StubModelExtractor(_current_system_prompt()))
    recalls = [s for s in run.metric_scores if s.metric_name == "dietary_recall"]
    assert len(recalls) == len(CASES)
    assert all(r.raw_values == [1.0] for r in recalls)


def test_a_degraded_template_that_stops_asking_for_dietary_data_drops_the_recall() -> None:
    degraded = _degrade(_current_system_prompt())
    assert "dietary" not in degraded
    run = _run(_StubModelExtractor(degraded))
    recalls = [s for s in run.metric_scores if s.metric_name == "dietary_recall"]
    assert len(recalls) == len(CASES)
    assert all(r.raw_values == [0.0] for r in recalls)


def test_the_degraded_template_fails_the_gate_when_dietary_recall_is_blocking() -> None:
    degraded_run = _run(_StubModelExtractor(_degrade(_current_system_prompt())))
    evaluate_strata(degraded_run, floors={"dietary_recall": 1.0, "intent_extraction_accuracy": 0.8})
    assert compute_verdict(degraded_run) == "FAIL"

    current_run = _run(_StubModelExtractor(_current_system_prompt()))
    evaluate_strata(current_run, floors={"dietary_recall": 1.0, "intent_extraction_accuracy": 0.8})
    assert compute_verdict(current_run) == "PASS"


def test_the_degraded_template_does_not_fail_the_gate_while_dietary_recall_is_report_only() -> None:
    """T053's documented interim state (before T053a's calibration flips the blocking constant):
    the metric is still computed and visible in the report, but cannot block a merge by itself."""
    degraded_run = _run(_StubModelExtractor(_degrade(_current_system_prompt())))
    evaluate_strata(
        degraded_run,
        floors={"dietary_recall": 1.0, "intent_extraction_accuracy": 0.8},
        report_only=frozenset({"dietary_recall"}),
    )
    assert compute_verdict(degraded_run) == "PASS"
    (stratum,) = [s for s in degraded_run.stratum_results if s.metric_name == "dietary_recall" and s.tenant_id is None]
    assert stratum.passed is False and stratum.blocking is False
