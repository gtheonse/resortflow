"""T028 -- prompt lineage attributes (contracts/telemetry-prompt-attributes.md, ADR-052 D5 option ii).

Exactly five `resortflow.prompt.*` attributes, emitted only by `start_generation_span`. No Langfuse prompt-link
attribute is ever emitted, and prompt text is never an attribute.
"""

from __future__ import annotations

from typing import Any

import pytest

from observability import attributes, telemetry
from prompts.registry import ResolvedPrompt

SOURCES = sorted(attributes.PROMPT_SOURCE_VALUES)
SYSTEM_TEXT = "SECRET SYSTEM PROMPT TEXT restriction in ['shellfish']"


@pytest.fixture(autouse=True)
def _isolated_tracer():  # type: ignore[no-untyped-def]
    exporter = telemetry.use_in_memory_exporter_for_tests()
    yield exporter
    telemetry.reset_for_tests()


def _prompt(source: str, *, reason: str | None = None) -> ResolvedPrompt:
    langfuse = source == "langfuse"
    return ResolvedPrompt(
        id="dining_intent_extraction",
        version="7" if langfuse else "git-0123456789ab",
        sha256="ab" * 32,
        source=source,
        messages=[{"role": "system", "content": SYSTEM_TEXT}],
        config={"temperature": 0},
        rejected_reason=reason,
        registry_version=7 if langfuse else None,
    )


def _emit(prompt: ResolvedPrompt, exporter: Any, **kw: Any) -> dict[str, Any]:
    with telemetry.start_generation_span("dining.intent_extraction", prompt=prompt, model="gpt-4.1-mini", node="intent_parser", **kw):
        pass
    (span,) = exporter.get_finished_spans()
    return dict(span.attributes)


@pytest.mark.parametrize("source", SOURCES)
def test_all_four_always_present_attributes_for_every_source(source: str, _isolated_tracer: Any) -> None:
    attrs = _emit(_prompt(source), _isolated_tracer)
    assert attrs[attributes.PROMPT_NAME] == "dining_intent_extraction"
    assert attrs[attributes.PROMPT_VERSION] == ("7" if source == "langfuse" else "git-0123456789ab")
    assert attrs[attributes.PROMPT_SHA256] == "ab" * 32
    assert attrs[attributes.PROMPT_SOURCE] == source
    assert attributes.PROMPT_REJECTED_REASON not in attrs


def test_rejected_reason_only_when_a_version_was_rejected(_isolated_tracer: Any) -> None:
    attrs = _emit(_prompt("git-fallback-unapproved", reason="unapproved"), _isolated_tracer)
    assert attrs[attributes.PROMPT_REJECTED_REASON] == "unapproved"
    assert attrs[attributes.PROMPT_SOURCE] == "git-fallback-unapproved"


def test_span_is_a_generation_with_the_model(_isolated_tracer: Any) -> None:
    attrs = _emit(_prompt("git"), _isolated_tracer, domain="dining", session_id="sess-1")
    assert attrs["langfuse.observation.type"] == "generation"
    assert attrs["gen_ai.request.model"] == "gpt-4.1-mini"
    assert attrs["resortflow.node"] == "intent_parser" and attrs["resortflow.domain"] == "dining"
    assert attrs["langfuse.session.id"] == "sess-1"


@pytest.mark.parametrize("source", SOURCES)
def test_no_langfuse_prompt_link_attribute_is_ever_emitted(source: str, _isolated_tracer: Any) -> None:
    attrs = _emit(_prompt(source), _isolated_tracer)
    assert not [key for key in attrs if key.startswith("langfuse.observation.prompt")]


def test_prompt_text_is_never_an_attribute(_isolated_tracer: Any) -> None:
    attrs = _emit(_prompt("git"), _isolated_tracer)
    assert not [k for k, v in attrs.items() if isinstance(v, str) and "SECRET SYSTEM PROMPT TEXT" in v]


def test_version_and_sha256_are_never_metric_dimensions() -> None:
    assert attributes.PROMPT_SHA256 not in attributes.BOUNDED_METRIC_DIMENSIONS
    assert attributes.PROMPT_VERSION not in attributes.BOUNDED_METRIC_DIMENSIONS


def test_attribute_names_are_defined_only_in_the_attributes_module() -> None:
    assert attributes.PROMPT_NAME == "resortflow.prompt.name"
    assert attributes.PROMPT_VERSION == "resortflow.prompt.version"
    assert attributes.PROMPT_SHA256 == "resortflow.prompt.sha256"
    assert attributes.PROMPT_SOURCE == "resortflow.prompt.source"
    assert attributes.PROMPT_REJECTED_REASON == "resortflow.prompt.rejected_reason"


@pytest.mark.skip(reason="US2 / T050a: the eval judge client gains a required `prompt` argument and span; not in the MVP scope")
def test_judge_call_emits_the_prompt_attributes() -> None:  # pragma: no cover - placeholder for T050a
    raise AssertionError("implement with T050a: OpenAICompatibleJudgeClient with a stubbed OpenAI client")
