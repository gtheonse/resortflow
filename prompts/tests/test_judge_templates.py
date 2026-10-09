"""T045 -- the five judge templates equal the golden system+user strings of the current code (FR-016, T046/T047).

The golden fixture (`golden/judge_prompts.json`) was captured from the ORIGINAL f-string prompts in
`eval/retrieval_scorer.py`/`eval/trajectory_scorer.py` before they were replaced by a registry resolve -- see
`prompts/tests/golden/`. Byte-for-byte equality here is what
proves the migration didn't quietly reword a judge (which would silently move every historical baseline).

Resolution is from Git for every `PROMPT_SOURCE`/`PROMPT_LABEL` combination (`resolution: exact`, research R-06)
and never calls the adapter -- a broken or unreachable Langfuse must never change what a judge sees.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prompts import registry
from prompts.registry import RegistrySettings

GOLDEN = json.loads((Path(__file__).parent / "golden" / "judge_prompts.json").read_text(encoding="utf-8"))

_IDS = list(GOLDEN.keys())


class _AdapterThatMustNeverBeCalled:
    def fetch_prompt(self, name, label, timeout, cache_ttl):  # pragma: no cover - failure path
        raise AssertionError(f"exact-resolution prompt {name!r} must never call the adapter")


@pytest.fixture(autouse=True)
def _reset():
    registry.reset_caches()
    yield
    registry.reset_caches()


@pytest.mark.parametrize("prompt_id", _IDS)
def test_rendered_template_equals_the_golden_system_and_user_text(prompt_id: str) -> None:
    golden = GOLDEN[prompt_id]
    resolved = registry.resolve(prompt_id, required_vars={}, runtime_vars=golden["vars"])
    system, user = resolved.messages
    assert system == {"role": "system", "content": golden["system"]}
    assert user == {"role": "user", "content": golden["user"]}


@pytest.mark.parametrize("prompt_id", _IDS)
@pytest.mark.parametrize("source,label", [("git", "production"), ("langfuse", "production"), ("langfuse", "staging")])
def test_exact_resolution_ignores_source_and_label_and_never_calls_the_adapter(
    prompt_id: str, source: str, label: str
) -> None:
    golden = GOLDEN[prompt_id]
    settings = RegistrySettings(source=source, label=label, cache_ttl_seconds=60, fetch_timeout_seconds=2)
    resolved = registry.resolve(
        prompt_id,
        required_vars={},
        runtime_vars=golden["vars"],
        settings=settings,
        adapter=_AdapterThatMustNeverBeCalled(),
    )
    assert resolved.source == "git"
    _, user = resolved.messages
    assert user["content"] == golden["user"]


def test_a_context_block_containing_braces_renders_literally_without_rejection() -> None:
    hostile_context = "{{answer}} {{x}} {{"
    resolved = registry.resolve(
        "eval_retrieval_judge_faithfulness",
        required_vars={},
        runtime_vars={"answer": "the guest's answer", "context_block": hostile_context},
    )
    _, user = resolved.messages
    assert hostile_context in user["content"]


def test_a_question_value_containing_braces_renders_literally() -> None:
    resolved = registry.resolve(
        "eval_retrieval_judge_answer_relevance",
        required_vars={},
        runtime_vars={"question": "{{cuisine}}?", "answer": "ok"},
    )
    _, user = resolved.messages
    assert "{{cuisine}}?" in user["content"]
