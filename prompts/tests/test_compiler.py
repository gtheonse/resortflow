"""T013 -- the strict, single-pass `{{name}}` compiler (research R-04, FR-009/009a/009b/011).

Rules under test:
* substitution is ONE regex pass over the template's own placeholders; runtime values are inserted
  verbatim and never rescanned, so guest text containing `{{...}}` is a value, not a template;
* `unrendered_placeholder` fires only when the TEMPLATE references a declared placeholder that has
  no supplied value (never because of what a value contains);
* code-injected (required) values win over caller-supplied values of the same name.
"""

from __future__ import annotations

from typing import Any

import pytest

from prompts.compiler import CompileError, compile_messages

DIETARY = str(["shellfish", "tree_nut", "peanut"])
SEVERITY = str(["preference", "allergy_severe"])


def _msgs(system: str, user: str = "{{guest_message}}") -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _compile(messages: list[dict[str, str]], **kw: Any) -> list[dict[str, str]]:
    kw.setdefault("required_vars", {"dietary_restrictions": DIETARY, "dietary_severities": SEVERITY})
    kw.setdefault("runtime_vars", {"guest_message": "table for two"})
    kw.setdefault("tenant_vars", {})
    kw.setdefault("declared", frozenset({"dietary_restrictions", "dietary_severities", "guest_message"}))
    return compile_messages(messages, **kw)


SYSTEM = "restriction in {{dietary_restrictions}} and severity in {{ dietary_severities }}."


def test_renders_with_and_without_inner_spaces() -> None:
    out = _compile(_msgs(SYSTEM))
    assert out[0]["content"] == f"restriction in {DIETARY} and severity in {SEVERITY}."
    assert out[1] == {"role": "user", "content": "table for two"}


def test_single_brace_json_is_left_untouched() -> None:
    system = 'Respond {"score": <float>, "reasoning": "x"} using {{dietary_restrictions}} {{dietary_severities}}'
    out = _compile(_msgs(system))
    assert '{"score": <float>, "reasoning": "x"}' in out[0]["content"]


def test_values_are_not_escaped() -> None:
    out = _compile(_msgs(SYSTEM, "{{guest_message}}"), runtime_vars={"guest_message": "it's \"quoted\" & <b>"})
    assert out[1]["content"] == "it's \"quoted\" & <b>"


def test_list_rendered_as_str_reproduces_old_fstring_form() -> None:
    out = _compile(_msgs(SYSTEM))
    assert "['shellfish', 'tree_nut', 'peanut']" in out[0]["content"]


def test_undeclared_variable_rejected() -> None:
    with pytest.raises(CompileError) as exc:
        _compile(_msgs(SYSTEM + " {{house_notes}}"))
    assert exc.value.reason == "undeclared_var"


def test_required_variable_missing_from_template_rejected() -> None:
    with pytest.raises(CompileError) as exc:
        _compile(_msgs("restriction in {{dietary_restrictions}} only."))  # severities never referenced
    assert exc.value.reason == "missing_required_var"


def test_declared_placeholder_without_supplied_value_is_unrendered() -> None:
    with pytest.raises(CompileError) as exc:
        _compile(_msgs(SYSTEM), runtime_vars={})  # template references {{guest_message}}, none supplied
    assert exc.value.reason == "unrendered_placeholder"


def test_code_injected_value_wins_over_caller_value() -> None:
    out = _compile(
        _msgs(SYSTEM),
        runtime_vars={"guest_message": "hi", "dietary_restrictions": "NOTHING"},
    )
    assert "NOTHING" not in out[0]["content"] and DIETARY in out[0]["content"]


def test_tenant_vars_render() -> None:
    out = _compile(
        _msgs(SYSTEM + " for {{display_name}}"),
        tenant_vars={"display_name": "Meridian Grand"},
        declared=frozenset({"dietary_restrictions", "dietary_severities", "guest_message", "display_name"}),
    )
    assert out[0]["content"].endswith("for Meridian Grand")


# --- FR-009b: runtime values are data, never templates ---------------------------------------

HOSTILE = "{{dietary_restrictions}} {{x}} {{"


def test_guest_text_with_braces_renders_literally_and_is_not_reexpanded() -> None:
    out = _compile(_msgs(SYSTEM), runtime_vars={"guest_message": HOSTILE})
    assert out[1]["content"] == HOSTILE  # literal, not expanded to the enum list
    assert DIETARY not in out[1]["content"]


def test_value_containing_braces_never_triggers_unrendered_placeholder() -> None:
    out = _compile(_msgs(SYSTEM), runtime_vars={"guest_message": "{{never_declared}}"})
    assert out[1]["content"] == "{{never_declared}}"


def test_single_pass_a_value_referencing_another_variable_is_not_expanded() -> None:
    out = _compile(
        _msgs("{{a}} then {{b}}", "u"),
        required_vars={},
        runtime_vars={"a": "{{b}}", "b": "B"},
        declared=frozenset({"a", "b"}),
    )
    assert out[0]["content"] == "{{b}} then B"


def test_compile_error_carries_reason_and_names() -> None:
    with pytest.raises(CompileError) as exc:
        _compile(_msgs(SYSTEM + " {{house_notes}} {{other}}"))
    assert exc.value.reason == "undeclared_var"
    assert set(exc.value.names) == {"house_notes", "other"}
