"""T019 -- the fail-closed matrix (contracts/registry-runtime.md; SC-004, FR-009/009a/009b/010/014/014a).

One case per row of the failure matrix. In every fault case the application must serve the version-controlled
copy, record why, and NEVER let the faulty content reach the model; no registry fault may raise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prompts.langfuse_adapter import AdapterError
from prompts.lock import LockState
from prompts.registry import PromptError, ResolvedPrompt, resolve
from prompts.tests.helpers import (
    PROMPT_ID,
    REQUIRED,
    FakeAdapter,
    hash_of,
    registry_prompt,
    settings,
    state_with,
    template_dict,
    write_templates,
)

GUEST = "table for two, no shellfish"


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    return write_templates(tmp_path / "templates", template_dict())


def _resolve(templates_dir: Path, adapter: Any, state: LockState, **kw: Any) -> ResolvedPrompt:
    kw.setdefault("runtime_vars", {"guest_message": GUEST})
    return resolve(
        PROMPT_ID,
        required_vars=REQUIRED,
        settings=settings("langfuse"),
        adapter=adapter,
        state=state,
        templates_dir=templates_dir,
        **kw,
    )


def _git_render(templates_dir: Path) -> list[dict[str, str]]:
    """What the version-controlled copy renders to -- the only acceptable fallback content."""
    got = resolve(
        PROMPT_ID,
        required_vars=REQUIRED,
        runtime_vars={"guest_message": GUEST},
        settings=settings("git"),
        state=state_with(),
        templates_dir=templates_dir,
    )
    assert got.source == "git"
    return got.messages


def _assert_served_git_copy(got: ResolvedPrompt, templates_dir: Path, *, source: str, reason: str) -> None:
    assert got.source == source
    assert got.rejected_reason == reason
    assert got.messages == _git_render(templates_dir)
    joined = " ".join(m["content"] for m in got.messages)
    assert "house_notes" not in joined and "{{" not in joined  # nothing stripped or unrendered got through
    assert REQUIRED["dietary_restrictions"] in joined  # the code-injected vocabulary always survives


# --- registry faults -----------------------------------------------------------------------------


def test_registry_down_or_timeout(templates_dir: Path) -> None:
    got = _resolve(templates_dir, FakeAdapter(AdapterError("fetch_error")), state_with())
    _assert_served_git_copy(got, templates_dir, source="git-fallback", reason="fetch_error")


def test_unexpected_adapter_exception_never_reaches_the_caller(templates_dir: Path) -> None:
    got = _resolve(templates_dir, FakeAdapter(boom=RuntimeError("adapter bug")), state_with())
    _assert_served_git_copy(got, templates_dir, source="git-fallback", reason="fetch_error")


def test_text_type_prompt_is_wrong_shape(templates_dir: Path) -> None:
    got = _resolve(templates_dir, FakeAdapter(AdapterError("wrong_shape")), state_with())
    _assert_served_git_copy(got, templates_dir, source="git-fallback", reason="wrong_shape")


# --- LOCKED registry content that fails the variable/config checks ---------------------------------
# (e.g. rolling back to an older approved version that lacks a variable the call site now requires)


def test_locked_hash_omitting_a_required_variable(templates_dir: Path) -> None:
    stripped = registry_prompt("restriction in {{dietary_restrictions}} only.")  # severities dropped
    got = _resolve(templates_dir, FakeAdapter(stripped), state_with(approved=[hash_of(stripped)]))
    _assert_served_git_copy(got, templates_dir, source="git-fallback", reason="missing_required_var")


def test_added_undeclared_variable(templates_dir: Path) -> None:
    extra = registry_prompt("restriction in {{dietary_restrictions}} and {{dietary_severities}} {{house_notes}}")
    got = _resolve(templates_dir, FakeAdapter(extra), state_with(approved=[hash_of(extra)]))
    _assert_served_git_copy(got, templates_dir, source="git-fallback", reason="undeclared_var")


def test_template_references_a_declared_variable_with_no_supplied_value(templates_dir: Path) -> None:
    unrendered = registry_prompt("{{dietary_restrictions}} {{dietary_severities}} {{locale}}")  # `locale` unsupplied
    got = _resolve(templates_dir, FakeAdapter(unrendered), state_with(approved=[hash_of(unrendered)]))
    _assert_served_git_copy(got, templates_dir, source="git-fallback", reason="unrendered_placeholder")


@pytest.mark.parametrize("bad_config", [{"model": "gpt-4.1"}, {"temperature": 0, "top_p": 0.9}, {"model_tier": "x"}])
def test_config_with_model_or_unknown_key(templates_dir: Path, bad_config: dict[str, Any]) -> None:
    prompt = registry_prompt("{{dietary_restrictions}} {{dietary_severities}}", config=bad_config)
    got = _resolve(templates_dir, FakeAdapter(prompt), state_with(approved=[hash_of(prompt)]))
    _assert_served_git_copy(got, templates_dir, source="git-fallback", reason="bad_config")


# --- allow-list and revocation -------------------------------------------------------------------


def test_hash_not_approved(templates_dir: Path) -> None:
    ok_looking = registry_prompt("{{dietary_restrictions}} {{dietary_severities}} edited in the UI")
    got = _resolve(templates_dir, FakeAdapter(ok_looking), state_with())  # nothing locked
    _assert_served_git_copy(got, templates_dir, source="git-fallback-unapproved", reason="unapproved")


def test_hash_revoked_even_if_also_approved(templates_dir: Path) -> None:
    prompt = registry_prompt("{{dietary_restrictions}} {{dietary_severities}} now revoked")
    digest = hash_of(prompt)
    got = _resolve(templates_dir, FakeAdapter(prompt), state_with(approved=[digest], revoked=[digest]))
    _assert_served_git_copy(got, templates_dir, source="git-fallback-revoked", reason="revoked")


def test_approved_clean_version_is_served(templates_dir: Path) -> None:
    """The control case: the same machinery serves registry content when it is locked and valid."""
    good = registry_prompt("{{dietary_restrictions}} {{dietary_severities}} approved wording", version=7)
    got = _resolve(templates_dir, FakeAdapter(good), state_with(approved=[hash_of(good)]))
    assert (got.source, got.rejected_reason, got.registry_version, got.version) == ("langfuse", None, 7, "7")
    assert "approved wording" in got.messages[0]["content"]


# --- the Git copy itself ---------------------------------------------------------------------------


def test_git_copy_invalid_raises(tmp_path: Path) -> None:
    broken = template_dict(messages=[{"role": "system", "content": "no required vars here"},
                                     {"role": "user", "content": "{{guest_message}}"}])
    templates_dir = write_templates(tmp_path / "templates", broken)
    with pytest.raises(PromptError):
        resolve(
            PROMPT_ID,
            required_vars=REQUIRED,
            runtime_vars={"guest_message": GUEST},
            settings=settings("git"),
            state=state_with(),
            templates_dir=templates_dir,
        )


def test_git_copy_revoked_raises(templates_dir: Path) -> None:
    from prompts.canonical import template_sha256
    from prompts.schema import load_all_templates

    digest = template_sha256(load_all_templates(templates_dir)[PROMPT_ID])
    with pytest.raises(PromptError):
        resolve(
            PROMPT_ID,
            required_vars=REQUIRED,
            runtime_vars={"guest_message": GUEST},
            settings=settings("git"),
            state=state_with(revoked=[digest]),
            templates_dir=templates_dir,
        )


def test_unknown_prompt_id_raises(templates_dir: Path) -> None:
    with pytest.raises(PromptError):
        resolve(
            "no_such_prompt",
            required_vars={},
            settings=settings("git"),
            state=state_with(),
            templates_dir=templates_dir,
        )


# --- FR-009b: guest text is data ------------------------------------------------------------------


@pytest.mark.parametrize("source", ["git", "langfuse"])
@pytest.mark.parametrize("guest", ["{{x}}", "{{dietary_restrictions}}", "a {{ b }} c {{", "{{{{", "}}"])
def test_guest_braces_are_served_normally_never_rejected(templates_dir: Path, source: str, guest: str) -> None:
    good = registry_prompt("{{dietary_restrictions}} {{dietary_severities}} approved", version=2)
    got = resolve(
        PROMPT_ID,
        required_vars=REQUIRED,
        runtime_vars={"guest_message": guest},
        settings=settings(source),
        adapter=FakeAdapter(good),
        state=state_with(approved=[hash_of(good)]),
        templates_dir=templates_dir,
    )
    assert got.rejected_reason is None  # no fallback, no error, no rejection
    assert got.source == source
    assert got.messages[1]["content"] == guest  # rendered literally, never re-expanded
