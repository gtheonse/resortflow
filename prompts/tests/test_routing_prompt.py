"""Feature 010, T010 -- the `intent_routing_classification` template
(`specs/010-tiered-intent-routing/contracts/routing-classifier-prompt.md`).

Its runtime variables are exactly `guest_message`/`previous_context`;
`allowed_kinds`/`allowed_services` are code-injected required vars (a
template missing either fails closed to the Git copy, same as
`agents/dining/tests/test_dietary_contract.py` does for the dining prompt);
it passes 009's lint (V1-V9); it resolves via `prompts.registry.resolve()`
in git mode; its owner is the new `platform` group (T004)."""

from __future__ import annotations

from pathlib import Path

from prompts import registry
from prompts.lint import lint_template, load_exemptions
from prompts.schema import PromptTemplate, load_template, placeholders_in

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "intent_routing_classification.yaml"
PROMPT_ID = "intent_routing_classification"


def _template() -> PromptTemplate:
    return load_template(TEMPLATE_PATH)


def test_runtime_variables_are_exactly_guest_message_and_previous_context() -> None:
    template = _template()
    assert set(template.variables.runtime) == {"guest_message", "previous_context"}
    assert template.variables.tenant == []


def test_allowed_kinds_and_services_are_required_not_declared_runtime_vars() -> None:
    template = _template()
    used = {name for message in template.messages for name in placeholders_in(message.content)}
    assert "allowed_kinds" in used
    assert "allowed_services" in used
    assert "allowed_kinds" not in template.variables.runtime
    assert "allowed_services" not in template.variables.runtime


def test_template_owner_is_platform_group() -> None:
    assert _template().owner == "platform"


def test_template_config_is_deterministic_json_object() -> None:
    template = _template()
    assert template.config.get("temperature") == 0
    assert template.config.get("response_format") == {"type": "json_object"}


def test_template_has_no_tools_and_family_is_routing() -> None:
    template = _template()
    assert template.family == "routing"
    assert template.type == "chat"


def test_template_passes_lint() -> None:
    template = _template()
    exemptions = load_exemptions()
    findings = lint_template(template, exemptions)
    assert findings == []


def test_resolves_via_registry_in_git_mode(monkeypatch) -> None:
    monkeypatch.delenv("PROMPT_SOURCE", raising=False)  # default is git
    registry.declare_required(PROMPT_ID, ["allowed_kinds", "allowed_services"])
    resolved = registry.resolve(
        PROMPT_ID,
        required_vars={
            "allowed_kinds": "book, lookup, modify, faq, out_of_scope, smalltalk",
            "allowed_services": "dining, entertainment, wellness, stay, gaming",
        },
        runtime_vars={"guest_message": "book a table for 4", "previous_context": "none"},
        templates_dir=TEMPLATE_PATH.parent,
    )
    assert resolved.source == "git"
    assert any("book a table for 4" in m["content"] for m in resolved.messages)


# --- Feature 011 (T005): browse ---------------------------------------------
# `specs/011-browse-requests/contracts/browse-classifier-prompt-delta.md`


def _render() -> str:
    from graph.models import Domain, RequestKind

    registry.declare_required(PROMPT_ID, ["allowed_kinds", "allowed_services"])
    resolved = registry.resolve(
        PROMPT_ID,
        required_vars={
            "allowed_kinds": ", ".join(k.value for k in RequestKind),
            "allowed_services": ", ".join(d.value for d in Domain),
        },
        runtime_vars={"guest_message": "list the resort experiences", "previous_context": "none"},
        templates_dir=TEMPLATE_PATH.parent,
    )
    return "\n".join(m["content"] for m in resolved.messages)


def test_rendered_prompt_lists_browse_among_the_injected_kinds() -> None:
    assert "browse" in _render().split("Guest:")[0]


def test_prompt_defines_browse_and_states_enumeration_is_in_scope() -> None:
    system = _render().split("Previous turn:")[0].lower()
    assert '"browse"' in system
    assert "never return \"out_of_scope\"" in system or "never return 'out_of_scope'" in system
    assert "in scope" in system


def test_browse_is_still_fail_closed_without_allowed_kinds() -> None:
    import pytest

    with pytest.raises(Exception):  # noqa: B017 - registry raises its own fail-closed error type
        registry.resolve(
            PROMPT_ID,
            required_vars={"allowed_services": "dining"},
            runtime_vars={"guest_message": "x", "previous_context": "none"},
            templates_dir=TEMPLATE_PATH.parent,
        )


def test_classifier_output_accepts_browse_and_rejects_browse_with_unknown_service() -> None:
    import pytest

    from graph.models import Domain, RequestKind
    from graph.routing.classifier import ClassifierOutput

    ok = ClassifierOutput(kind=RequestKind.BROWSE, services=[Domain.DINING])
    assert ok.kind == RequestKind.BROWSE
    with pytest.raises(Exception):  # noqa: B017 - registry raises its own fail-closed error type
        ClassifierOutput.model_validate({"kind": "browse", "services": ["casino_cruise"]})
