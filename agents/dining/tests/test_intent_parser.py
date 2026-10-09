"""T046 unit tests — rule-based extractor (the credential-fallback path;
see agents/dining/nodes/intent_parser.py's module docstring)."""

from __future__ import annotations

from datetime import date

import pytest

from agents.dining.nodes.intent_parser import RuleBasedIntentExtractor
from graph.models import DietaryRestriction, DietarySeverity

TODAY = date(2026, 9, 11)
EXTRACTOR = RuleBasedIntentExtractor()


@pytest.mark.parametrize(
    "text,expected_party,expected_cuisine",
    [
        ("Table for 4 tomorrow at 7pm, steakhouse please", 4, "steakhouse"),
        ("Party of 2 tonight at 19:30, japanese food", 2, "japanese"),
        ("We need a table for 6 for a birthday dinner", 6, None),
        ("Book mexican for 3 on 2026-12-01 at 6:15pm", 3, "mexican"),
        ("Anniversary dinner for two at Sakura tonight", None, None),
        ("Table for 10, vegetarian, halal too", 10, "vegetarian"),
        ("for 5 tomorrow, italian, gluten allergy severe", 5, "italian"),
        ("party of 1 tonight, sushi, peanut allergy", 1, "sushi"),
        ("graduation dinner for 8 tomorrow at 8pm", 8, None),
        ("table for 4 at 20:00 today, asian fusion, vegan", 4, "asian_fusion"),
    ],
)
def test_ten_sample_requests(text, expected_party, expected_cuisine):
    intent = EXTRACTOR.extract(text, today=TODAY)
    assert intent["party_size"] == expected_party
    assert intent["cuisine"] == expected_cuisine


def test_missing_party_and_date_are_flagged_ambiguous():
    intent = EXTRACTOR.extract("Anniversary dinner at Sakura", today=TODAY)
    assert "party_size" in intent["ambiguous_fields"]
    assert "date_str" in intent["ambiguous_fields"]


def test_tonight_and_tomorrow_resolve_relative_to_today():
    assert EXTRACTOR.extract("table for 2 tonight", today=TODAY)["date_str"] == "2026-09-11"
    assert EXTRACTOR.extract("table for 2 tomorrow", today=TODAY)["date_str"] == "2026-09-12"


def test_iso_date_is_extracted_verbatim():
    intent = EXTRACTOR.extract("table for 2 on 2026-10-05", today=TODAY)
    assert intent["date_str"] == "2026-10-05"


def test_severe_peanut_allergy_extracts_structured_requirement_not_free_text_only():
    intent = EXTRACTOR.extract("table for 2 tomorrow, severe peanut allergy", today=TODAY)
    dietary = intent["dietary"]
    assert len(dietary) == 1
    req = dietary[0]
    assert req.restriction == DietaryRestriction.PEANUT
    assert req.severity == DietarySeverity.ALLERGY_SEVERE
    assert req.detail  # free text is supplementary, present but not the sole carrier


def test_unqualified_allergen_mention_defaults_to_moderate_not_preference():
    intent = EXTRACTOR.extract("table for 2 tomorrow, shellfish", today=TODAY)
    req = intent["dietary"][0]
    assert req.restriction == DietaryRestriction.SHELLFISH
    assert req.severity == DietarySeverity.ALLERGY_MODERATE


def test_lifestyle_restriction_defaults_to_preference():
    intent = EXTRACTOR.extract("table for 2 tomorrow, vegetarian", today=TODAY)
    req = intent["dietary"][0]
    assert req.restriction == DietaryRestriction.VEGETARIAN
    assert req.severity == DietarySeverity.PREFERENCE


def test_intolerance_marker_is_distinguished_from_allergy():
    intent = EXTRACTOR.extract("table for 2 tomorrow, dairy intolerance", today=TODAY)
    req = intent["dietary"][0]
    assert req.severity == DietarySeverity.INTOLERANCE


def test_multiple_dietary_requirements_all_captured():
    intent = EXTRACTOR.extract(
        "table for 4 tomorrow, severe tree nut allergy, also vegan", today=TODAY
    )
    restrictions = {req.restriction for req in intent["dietary"]}
    assert restrictions == {DietaryRestriction.TREE_NUT, DietaryRestriction.VEGAN}


# --- AzureIntentExtractor: the prompt now comes from the registry (feature 009, T035) ----------------------------


class _StubCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        from types import SimpleNamespace

        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _azure_extractor(content: str):
    """An `AzureIntentExtractor` with a stubbed client (the constructor would import the OpenAI SDK)."""
    from types import SimpleNamespace

    from agents.dining.nodes.intent_parser import AzureIntentExtractor

    completions = _StubCompletions(content)
    extractor = AzureIntentExtractor.__new__(AzureIntentExtractor)
    extractor._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    extractor._deployment = "gpt-4.1-mini"
    return extractor, completions


PAYLOAD = (
    '{"cuisine": "steakhouse", "party_size": 4, "date_str": "2026-09-12", "time_str": "19:00", '
    '"occasion": null, "dietary": [{"restriction": "peanut", "severity": "allergy_severe", "detail": "peanut"}]}'
)


def test_azure_extractor_sends_the_registry_prompt_and_config():
    extractor, completions = _azure_extractor(PAYLOAD)
    intent = extractor.extract("Table for 4 tomorrow, severe peanut allergy", today=TODAY)
    (call,) = completions.calls
    assert call["model"] == "gpt-4.1-mini"
    assert call["temperature"] == 0 and call["response_format"] == {"type": "json_object"}
    system, user = call["messages"]
    assert system["role"] == "system" and str([r.value for r in DietaryRestriction]) in system["content"]
    assert str([s.value for s in DietarySeverity]) in system["content"]
    assert user == {"role": "user", "content": "Table for 4 tomorrow, severe peanut allergy"}
    # output validation is unchanged: the structured payload becomes a DietaryRequirement
    assert intent["dietary"][0].restriction == DietaryRestriction.PEANUT
    assert intent["dietary"][0].severity == DietarySeverity.ALLERGY_SEVERE


def test_azure_extractor_model_call_is_wrapped_in_a_prompt_lineage_span():
    from observability import telemetry

    exporter = telemetry.use_in_memory_exporter_for_tests()
    try:
        extractor, _ = _azure_extractor(PAYLOAD)
        extractor.extract("Table for 2 tonight", today=TODAY)
        (span,) = exporter.get_finished_spans()
        attrs = dict(span.attributes)
        assert attrs["resortflow.prompt.name"] == "dining_intent_extraction"
        assert attrs["resortflow.prompt.source"] == "git"
        assert len(attrs["resortflow.prompt.sha256"]) == 64
        assert attrs["langfuse.observation.type"] == "generation"
        assert attrs["gen_ai.request.model"] == "gpt-4.1-mini"
    finally:
        telemetry.reset_for_tests()


def test_guest_text_containing_braces_is_sent_literally_and_never_rejected():
    extractor, completions = _azure_extractor(PAYLOAD)
    hostile = "{{dietary_restrictions}} {{x}} {{"
    extractor.extract(hostile, today=TODAY)
    (call,) = completions.calls
    assert call["messages"][1] == {"role": "user", "content": hostile}
