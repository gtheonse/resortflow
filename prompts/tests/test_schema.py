"""T011 -- template schema validation (contracts/prompt-template-schema.md V1, V7, owner keys, ids).

V8 (tenant variables must be TenantProfile fields) is tested in test_tenant.py, next to the
closed tenant model it depends on (T014/T018).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from prompts.schema import PromptTemplate, TemplateError, load_all_templates, load_template

OWNERS: dict[str, Any] = {"compliance": {}, "eval_track": {}, "incident": {}}


def _base(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "dining_intent_extraction",
        "family": "dining",
        "type": "chat",
        "resolution": "label",
        "owner": "compliance",
        "config": {"temperature": 0, "response_format": {"type": "json_object"}},
        "variables": {"runtime": ["guest_message"], "tenant": []},
        "messages": [
            {"role": "system", "content": "Extract intent for {{dietary_restrictions}}."},
            {"role": "user", "content": "{{guest_message}}"},
        ],
    }
    data.update(overrides)
    return data


def _write(tmp_path: Path, data: dict[str, Any], name: str | None = None) -> Path:
    path = tmp_path / f"{name or data['id']}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_valid_template_loads(tmp_path: Path) -> None:
    template = load_template(_write(tmp_path, _base()), owners=OWNERS)
    assert isinstance(template, PromptTemplate)
    assert template.id == "dining_intent_extraction"
    assert template.messages[0].role == "system"
    assert template.variables.runtime == ["guest_message"]


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):
        load_template(_write(tmp_path, _base(surprise="x")), owners=OWNERS)


@pytest.mark.parametrize(
    "config",
    [
        {"model": "gpt-4.1"},  # model selection is declared in code, never in a prompt (S-1)
        {"model_tier": "frontier"},  # runtime tier is static per node (Principle VI)
        {"temperature": 0, "top_p": 0.9},  # any key outside the allow-list
    ],
)
def test_config_key_outside_allow_list_rejected(tmp_path: Path, config: dict[str, Any]) -> None:
    with pytest.raises(TemplateError):
        load_template(_write(tmp_path, _base(config=config)), owners=OWNERS)


def test_config_allow_list_keys_accepted(tmp_path: Path) -> None:
    template = load_template(_write(tmp_path, _base(config={"temperature": 0.2})), owners=OWNERS)
    assert template.config == {"temperature": 0.2}


def test_exact_resolution_only_for_judge_families(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):  # V7: dining is label-resolved
        load_template(_write(tmp_path, _base(resolution="exact")), owners=OWNERS)
    judge = _base(
        id="eval_retrieval_judge_faithfulness",
        family="eval_retrieval_judge",
        resolution="exact",
        owner="eval_track",
        variables={"runtime": ["answer"], "tenant": []},
        messages=[{"role": "system", "content": "Judge {{answer}}"}],
    )
    assert load_template(_write(tmp_path, judge), owners=OWNERS).resolution == "exact"


def test_owner_must_be_a_key_in_owners(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):
        load_template(_write(tmp_path, _base(owner="nobody")), owners=OWNERS)


@pytest.mark.parametrize("bad_id", ["Bad-Id", "a/b", "1starts_with_digit", "has space", "UPPER"])
def test_id_must_be_snake_case_without_slash(tmp_path: Path, bad_id: str) -> None:
    with pytest.raises(TemplateError):
        load_template(_write(tmp_path, _base(id=bad_id), name="whatever"), owners=OWNERS)


def test_id_must_equal_filename_stem(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):
        load_template(_write(tmp_path, _base(), name="different_name"), owners=OWNERS)


def test_message_roles_and_shape(tmp_path: Path) -> None:
    with pytest.raises(TemplateError):  # only system/user roles
        load_template(
            _write(tmp_path, _base(messages=[{"role": "assistant", "content": "x"}])), owners=OWNERS
        )
    with pytest.raises(TemplateError):  # at least one message
        load_template(_write(tmp_path, _base(messages=[])), owners=OWNERS)
    with pytest.raises(TemplateError):  # type must be chat
        load_template(_write(tmp_path, _base(type="text")), owners=OWNERS)


def test_load_all_templates_returns_by_id(tmp_path: Path) -> None:
    _write(tmp_path, _base())
    templates = load_all_templates(tmp_path, owners=OWNERS)
    assert set(templates) == {"dining_intent_extraction"}


def test_placeholders_regex_allows_inner_spaces() -> None:
    from prompts.schema import placeholders_in

    assert placeholders_in("a {{x}} b {{ y }} c {single} {{ 1bad }}") == ["x", "y"]
