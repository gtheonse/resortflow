"""T024 -- the template lint (FR-028e, FR-012d; Principle IV).

Policy must not live in prompt text: a template may not name a dietary restriction/severity value (the
vocabulary is injected from code), a tenant identifier, or a jurisdiction/regulator. Exemptions are explicit,
reviewed, per-token AND per-prompt, and carry a reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from graph.models import DietaryRestriction, DietarySeverity
from prompts.lint import (
    Exemption,
    LintConfigError,
    dietary_tokens,
    lint_template,
    load_exemptions,
)
from prompts.schema import PromptTemplate


def _template(text: str, *, prompt_id: str = "dining_intent_extraction") -> PromptTemplate:
    data: dict[str, Any] = {
        "id": prompt_id,
        "family": "dining",
        "type": "chat",
        "resolution": "label",
        "owner": "compliance",
        "config": {},
        "variables": {"runtime": ["guest_message"], "tenant": []},
        "messages": [{"role": "system", "content": text}, {"role": "user", "content": "{{guest_message}}"}],
    }
    return PromptTemplate.model_validate(data)


def _tokens(findings: list[Any]) -> set[str]:
    return {f.token.lower() for f in findings}


def test_clean_template_has_no_findings() -> None:
    clean = "Extract intent using restriction in {{dietary_restrictions}} and severity in {{dietary_severities}}."
    assert lint_template(_template(clean), []) == []


def test_literal_dietary_values_are_flagged() -> None:
    findings = lint_template(_template("We accommodate gluten, nuts, shellfish and vegan guests."), [])
    assert {"gluten", "shellfish", "vegan"} <= _tokens(findings)
    assert all(f.prompt_id == "dining_intent_extraction" for f in findings)


def test_matching_is_word_bounded_and_case_insensitive() -> None:
    assert "gluten" in _tokens(lint_template(_template("GLUTEN is fine"), []))
    assert lint_template(_template("the eggplant and soybeans are not values"), []) == []  # no substring hits


def test_ordinary_word_collision_preference_is_flagged_until_exempted() -> None:
    text = "Honour the guest's seating preference."
    assert "preference" in _tokens(lint_template(_template(text), []))


def test_exemption_suppresses_exactly_that_token_in_that_prompt() -> None:
    exemptions = [Exemption(prompt="dining_intent_extraction", token="preference", reason="ordinary English word")]
    text = "Honour the seating preference, also gluten."
    assert _tokens(lint_template(_template(text), exemptions)) == {"gluten"}  # preference suppressed, gluten not
    # same token, different prompt -> still flagged
    other = _template("seating preference", prompt_id="eval_retrieval_judge_faithfulness")
    assert "preference" in _tokens(lint_template(other, exemptions))


def test_exemptions_require_a_reason(tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    path.write_text(yaml.safe_dump({"exemptions": [{"prompt": "p", "token": "preference", "reason": " "}]}), encoding="utf-8")
    with pytest.raises(LintConfigError):
        load_exemptions(path)
    path.write_text(yaml.safe_dump({"exemptions": [{"prompt": "p", "token": "preference"}]}), encoding="utf-8")
    with pytest.raises(LintConfigError):
        load_exemptions(path)


def test_load_exemptions_roundtrip_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "ex.yaml"
    path.write_text(
        yaml.safe_dump({"exemptions": [{"prompt": "p", "token": "Preference", "reason": "English"}]}), encoding="utf-8"
    )
    assert load_exemptions(path) == [Exemption(prompt="p", token="preference", reason="English")]
    assert load_exemptions(tmp_path / "absent.yaml") == []


@pytest.mark.parametrize("text", ["for MGV guests", "the MHB property", "Nevada rules apply", "per NGCB", "Massachusetts MGC"])
def test_tenant_and_jurisdiction_identifiers_are_flagged(text: str) -> None:
    assert lint_template(_template(text), []), text


def test_dietary_tokens_are_derived_from_the_live_enumerations() -> None:
    expected = {m.value for m in DietaryRestriction} | {m.value for m in DietarySeverity}
    assert set(dietary_tokens()) == expected
    assert "low_sodium" in dietary_tokens() and "allergy_severe" in dietary_tokens()


def test_placeholders_are_not_linted() -> None:
    assert lint_template(_template("restriction in {{dietary_restrictions}}"), []) == []
