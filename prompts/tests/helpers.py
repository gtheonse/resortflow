"""Shared fixtures for the registry tests: a dining-like template, a fake registry adapter and lock state.

Tests never touch the real Langfuse SDK except test_adapter_tracing_disabled.py, and they never depend on
the real `prompts/templates/` contents (those are covered by the golden and contract tests).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from prompts.canonical import sha256_of
from prompts.langfuse_adapter import AdapterError, RegistryPrompt
from prompts.lock import LockState
from prompts.registry import RegistrySettings

PROMPT_ID = "dining_intent_extraction"
SYSTEM = "restriction in {{dietary_restrictions}} and severity in {{dietary_severities}}."
REQUIRED = {"dietary_restrictions": "['shellfish', 'peanut']", "dietary_severities": "['allergy_severe']"}
CONFIG: dict[str, Any] = {"temperature": 0, "response_format": {"type": "json_object"}}


def template_dict(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": PROMPT_ID,
        "family": "dining",
        "type": "chat",
        "resolution": "label",
        "owner": "compliance",
        "config": dict(CONFIG),
        # `locale` is declared but unused by the Git copy, so a registry variant can reference it
        # (declared, no value supplied) to exercise `unrendered_placeholder`.
        "variables": {"runtime": ["guest_message", "locale"], "tenant": []},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "{{guest_message}}"},
        ],
    }
    data.update(over)
    return data


def judge_template_dict() -> dict[str, Any]:
    return {
        "id": "eval_retrieval_judge_faithfulness",
        "family": "eval_retrieval_judge",
        "type": "chat",
        "resolution": "exact",
        "owner": "eval_track",
        "config": {"temperature": 0},
        "variables": {"runtime": ["answer"], "tenant": []},
        "messages": [{"role": "system", "content": "Judge this answer: {{answer}}"}],
    }


def write_templates(directory: Path, *templates: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for data in templates:
        (directory / f"{data['id']}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return directory


def git_messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "{{guest_message}}"},
    ]


def variant(system: str, config: dict[str, Any] | None = None) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Registry-side content: same user message, a different system message."""
    return (
        [{"role": "system", "content": system}, {"role": "user", "content": "{{guest_message}}"}],
        dict(CONFIG) if config is None else config,
    )


def registry_prompt(system: str, config: dict[str, Any] | None = None, *, version: int = 3) -> RegistryPrompt:
    messages, cfg = variant(system, config)
    return RegistryPrompt(name=PROMPT_ID, version=version, messages=messages, config=cfg, labels=["production"])


def hash_of(prompt: RegistryPrompt) -> str:
    return sha256_of(prompt.messages, prompt.config)


def state_with(*, approved: list[str] = (), revoked: list[str] = ()) -> LockState:  # type: ignore[assignment]
    return LockState(
        approved={PROMPT_ID: frozenset(approved)},
        revoked={PROMPT_ID: frozenset(revoked)},
    )


def settings(source: str = "langfuse") -> RegistrySettings:
    return RegistrySettings(source=source, label="production", cache_ttl_seconds=60, fetch_timeout_seconds=2)


class FakeAdapter:
    """Stands in for `LangfuseAdapter`. `result` is what `fetch_prompt` returns; `boom` is raised instead."""

    def __init__(self, result: RegistryPrompt | AdapterError | None = None, *, boom: Exception | None = None):
        self.result = result
        self.boom = boom
        self.calls: list[tuple[str, str, int, int]] = []

    def fetch_prompt(self, name: str, label: str, timeout: int, cache_ttl: int) -> RegistryPrompt | AdapterError:
        self.calls.append((name, label, timeout, cache_ttl))
        if self.boom is not None:
            raise self.boom
        assert self.result is not None
        return self.result
