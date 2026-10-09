"""T020 -- the dining call site's mandatory dietary variables (FR-007/FR-008/FR-012c; Principle IV).

The dietary restriction and severity vocabularies are policy, not prompt wording. They are declared HERE, in
code, and injected from the code-defined enumerations on every call -- a prompt edit (in Git or in the registry)
can never remove the obligation or change which restrictions exist.

Named `test_dietary_*` on purpose: the FR-042 ownership glob `agents/dining/tests/test_dietary_*.py` puts the
compliance owner on this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.dining.nodes.intent_parser import (
    DINING_INTENT_PROMPT_ID,
    DINING_INTENT_REQUIRED_NAMES,
    dining_intent_required_vars,
)
from graph.models import DietaryRestriction, DietarySeverity
from prompts.compiler import CompileError, compile_messages
from prompts.schema import load_all_templates

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "prompts" / "templates"


def test_required_vars_are_exactly_the_two_dietary_vocabularies() -> None:
    required = dining_intent_required_vars()
    assert set(required) == {"dietary_restrictions", "dietary_severities"}
    assert set(DINING_INTENT_REQUIRED_NAMES) == set(required)


def test_required_var_values_come_from_the_code_enumerations() -> None:
    required = dining_intent_required_vars()
    assert required["dietary_restrictions"] == str([r.value for r in DietaryRestriction])
    assert required["dietary_severities"] == str([s.value for s in DietarySeverity])


def test_required_names_are_declared_with_the_registry() -> None:
    """`prompts.sync validate` and `graph/api.py` startup check the Git template against these declarations."""
    from prompts.registry import required_names

    assert required_names(DINING_INTENT_PROMPT_ID) == frozenset({"dietary_restrictions", "dietary_severities"})


def test_template_variables_are_a_subset_of_required_plus_runtime() -> None:
    template = load_all_templates(TEMPLATES_DIR)[DINING_INTENT_PROMPT_ID]
    from prompts.schema import placeholders_in

    used = {name for m in template.messages for name in placeholders_in(m.content)}
    allowed = set(dining_intent_required_vars()) | set(template.variables.runtime)
    assert used <= allowed, f"undeclared placeholders: {sorted(used - allowed)}"
    assert template.variables.tenant == []  # FR-012c: no tenant variables today
    assert template.owner == "compliance"  # FR-036a


@pytest.mark.parametrize("dropped", ["dietary_restrictions", "dietary_severities"])
def test_a_template_without_either_required_variable_is_rejected(dropped: str) -> None:
    template = load_all_templates(TEMPLATES_DIR)[DINING_INTENT_PROMPT_ID]
    messages = [{"role": m.role, "content": m.content.replace("{{" + dropped + "}}", "")} for m in template.messages]
    required = dining_intent_required_vars()
    with pytest.raises(CompileError) as exc:
        compile_messages(
            messages,
            required_vars=required,
            runtime_vars={"guest_message": "hi"},
            tenant_vars={},
            declared=set(required) | set(template.variables.runtime),
        )
    assert exc.value.reason == "missing_required_var"
    assert dropped in exc.value.names
