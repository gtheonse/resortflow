"""T043 -- deterministic dining-intent metrics (research R-08, FR-035). A fake extractor stands in
for `AzureIntentExtractor` (mirrors every other eval scorer's `FakeJudgeClient` convention) — no
network call, no credentials required, no judge model involved at all."""

from __future__ import annotations

from datetime import date

from eval.intent_scorer import score_intent_case, score_intent_cases
from graph.models import DietaryRequirement, DietaryRestriction, DietarySeverity

TODAY = date(2026, 1, 15)


class _FakeExtractor:
    """Returns a canned `IntentParsed`-shaped dict regardless of input text — deterministic and
    inspectable, the same "fakes" pattern every other domain runner already uses."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def extract(self, text: str, *, today: date) -> dict:
        return dict(self.payload)


def _case(**over) -> dict:
    base = {
        "id": "c1",
        "tenant_id": "MGV",
        "text": "table for 4 tomorrow at 7pm, steakhouse, severe peanut allergy",
        "expected": {
            "cuisine": "steakhouse",
            "party_size": 4,
            "date_str": "2026-01-16",
            "time_str": "19:00",
            "dietary": [{"restriction": "peanut", "severity": "allergy_severe"}],
        },
    }
    base.update(over)
    return base


def _payload(**over) -> dict:
    base = {
        "cuisine": "steakhouse",
        "party_size": 4,
        "date_str": "2026-01-16",
        "time_str": "19:00",
        "occasion": None,
        "dietary": [
            DietaryRequirement(
                restriction=DietaryRestriction.PEANUT, severity=DietarySeverity.ALLERGY_SEVERE, detail="peanut"
            )
        ],
        "ambiguous_fields": [],
    }
    base.update(over)
    return base


def test_a_perfect_extraction_scores_1_0_on_both_metrics() -> None:
    scores = score_intent_case(_case(), extractor=_FakeExtractor(_payload()), today=TODAY)
    by_name = {s.metric_name: s for s in scores}
    assert by_name["intent_extraction_accuracy"].raw_values == [1.0]
    assert by_name["dietary_recall"].raw_values == [1.0]
    assert by_name["intent_extraction_accuracy"].tenant_id == "MGV"
    assert by_name["dietary_recall"].tenant_id == "MGV"


def test_intent_extraction_accuracy_is_exact_match_over_the_four_fields() -> None:
    scores = score_intent_case(_case(), extractor=_FakeExtractor(_payload(cuisine="japanese")), today=TODAY)
    (accuracy,) = [s for s in scores if s.metric_name == "intent_extraction_accuracy"]
    assert accuracy.raw_values == [0.75]  # 3 of 4 fields (party_size, date_str, time_str) match


def test_no_model_judge_is_called() -> None:
    scores = score_intent_case(_case(), extractor=_FakeExtractor(_payload()), today=TODAY)
    assert all(s.judge_model.provider == "" and s.judge_model.model_id == "" for s in scores)


def test_dropping_one_stated_restriction_gives_recall_below_1() -> None:
    case = _case(
        expected={
            "cuisine": "steakhouse", "party_size": 4, "date_str": "2026-01-16", "time_str": "19:00",
            "dietary": [
                {"restriction": "peanut", "severity": "allergy_severe"},
                {"restriction": "shellfish", "severity": "allergy_moderate"},
            ],
        }
    )
    scores = score_intent_case(case, extractor=_FakeExtractor(_payload()), today=TODAY)  # payload has only peanut
    (recall,) = [s for s in scores if s.metric_name == "dietary_recall"]
    assert recall.raw_values == [0.5]


def test_a_wrong_severity_counts_as_a_miss_not_a_partial_match() -> None:
    case = _case(
        expected={
            "cuisine": "steakhouse", "party_size": 4, "date_str": "2026-01-16", "time_str": "19:00",
            "dietary": [{"restriction": "peanut", "severity": "allergy_severe"}],
        }
    )
    wrong_severity = _payload(
        dietary=[DietaryRequirement(restriction=DietaryRestriction.PEANUT, severity=DietarySeverity.PREFERENCE)]
    )
    scores = score_intent_case(case, extractor=_FakeExtractor(wrong_severity), today=TODAY)
    (recall,) = [s for s in scores if s.metric_name == "dietary_recall"]
    assert recall.raw_values == [0.0]


def test_no_stated_dietary_requirements_gives_a_perfect_recall() -> None:
    case = _case(expected={"cuisine": None, "party_size": None, "date_str": None, "time_str": None, "dietary": []})
    scores = score_intent_case(case, extractor=_FakeExtractor(_payload(dietary=[])), today=TODAY)
    (recall,) = [s for s in scores if s.metric_name == "dietary_recall"]
    assert recall.raw_values == [1.0]


def test_both_metrics_are_tenant_stratified() -> None:
    mgv = _case(id="c1", tenant_id="MGV")
    mhb = _case(id="c2", tenant_id="MHB")
    scores = score_intent_cases([mgv, mhb], extractor=_FakeExtractor(_payload()), today=TODAY)
    tenants = {s.tenant_id for s in scores}
    assert tenants == {"MGV", "MHB"}


def test_score_intent_cases_flattens_every_case() -> None:
    cases = [_case(id="c1"), _case(id="c2"), _case(id="c3")]
    scores = score_intent_cases(cases, extractor=_FakeExtractor(_payload()), today=TODAY)
    assert len(scores) == 6  # 2 metrics x 3 cases
    assert {s.conversation_id for s in scores} == {"c1", "c2", "c3"}
