"""T025 -- source selection in `prompts.registry.resolve` (contracts/registry-runtime.md).

Caching is owned by the Langfuse SDK (see test_adapter_calls.py); the project adds no second cache (FR-006),
so there is deliberately no cache assertion here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prompts.canonical import template_sha256
from prompts.langfuse_adapter import LangfuseAdapter
from prompts.registry import RegistrySettings, ResolvedPrompt, resolve
from prompts.schema import load_all_templates
from prompts.tests.helpers import (
    PROMPT_ID,
    REQUIRED,
    FakeAdapter,
    hash_of,
    judge_template_dict,
    registry_prompt,
    settings,
    state_with,
    template_dict,
    write_templates,
)


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    return write_templates(tmp_path / "templates", template_dict(), judge_template_dict())


def _resolve(templates_dir: Path, **kw: object) -> ResolvedPrompt:
    kw.setdefault("required_vars", REQUIRED)
    kw.setdefault("runtime_vars", {"guest_message": "hello"})
    kw.setdefault("state", state_with())
    return resolve(PROMPT_ID, templates_dir=templates_dir, **kw)  # type: ignore[arg-type]


def test_git_source_serves_the_file(templates_dir: Path) -> None:
    got = _resolve(templates_dir, settings=settings("git"))
    digest = template_sha256(load_all_templates(templates_dir)[PROMPT_ID])
    assert got.source == "git"
    assert got.sha256 == digest
    assert got.version == f"git-{digest[:12]}"
    assert got.rejected_reason is None and got.registry_version is None


def test_git_source_never_calls_the_registry(templates_dir: Path) -> None:
    adapter = FakeAdapter(registry_prompt("x"))
    _resolve(templates_dir, settings=settings("git"), adapter=adapter)
    assert adapter.calls == []


def test_langfuse_source_serves_an_approved_registry_version(templates_dir: Path) -> None:
    approved = registry_prompt("{{dietary_restrictions}} {{dietary_severities}} registry wording", version=11)
    adapter = FakeAdapter(approved)
    got = _resolve(templates_dir, settings=settings("langfuse"), adapter=adapter, state=state_with(approved=[hash_of(approved)]))
    assert got.source == "langfuse"
    assert got.version == "11" and got.registry_version == 11  # version = registry integer as text
    assert got.sha256 == hash_of(approved)
    assert "registry wording" in got.messages[0]["content"]
    assert adapter.calls == [(PROMPT_ID, "production", 2, 60)]  # label + timeout + ttl come from settings


def test_label_and_timeouts_come_from_settings(templates_dir: Path) -> None:
    approved = registry_prompt("{{dietary_restrictions}} {{dietary_severities}} x")
    adapter = FakeAdapter(approved)
    custom = RegistrySettings(source="langfuse", label="staging", cache_ttl_seconds=5, fetch_timeout_seconds=1)
    _resolve(templates_dir, settings=custom, adapter=adapter, state=state_with(approved=[hash_of(approved)]))
    assert adapter.calls == [(PROMPT_ID, "staging", 1, 5)]


def test_registry_credentials_absent_falls_back_to_git(templates_dir: Path) -> None:
    got = _resolve(templates_dir, settings=settings("langfuse"), adapter=LangfuseAdapter(env={}))
    assert got.source == "git-fallback"
    assert got.rejected_reason == "fetch_error"  # credentials/host absent is an unreachable registry


def test_exact_policy_prompts_never_call_the_adapter(templates_dir: Path) -> None:
    adapter = FakeAdapter(registry_prompt("x"))
    got = resolve(
        "eval_retrieval_judge_faithfulness",
        required_vars={},
        runtime_vars={"answer": "the answer"},
        settings=settings("langfuse"),
        adapter=adapter,
        state=state_with(),
        templates_dir=templates_dir,
    )
    assert adapter.calls == []  # judges always resolve from Git (research R-06)
    assert got.source == "git"
    assert got.messages == [{"role": "system", "content": "Judge this answer: the answer"}]


def test_settings_from_env_defaults_and_validation() -> None:
    d = RegistrySettings.from_env({})
    assert (d.source, d.label, d.cache_ttl_seconds, d.fetch_timeout_seconds) == ("git", "production", 60, 2)
    e = RegistrySettings.from_env(
        {"PROMPT_SOURCE": "langfuse", "PROMPT_LABEL": "staging", "PROMPT_CACHE_TTL_SECONDS": "5", "PROMPT_FETCH_TIMEOUT_SECONDS": "1"}
    )
    assert (e.source, e.label, e.cache_ttl_seconds, e.fetch_timeout_seconds) == ("langfuse", "staging", 5, 1)
    # an unknown source, or a garbage number, must never crash a turn: fall back to the safe defaults
    bad = RegistrySettings.from_env({"PROMPT_SOURCE": "bogus", "PROMPT_CACHE_TTL_SECONDS": "abc"})
    assert (bad.source, bad.cache_ttl_seconds) == ("git", 60)
