"""T033 -- `resolve()`: the only interface agents and eval code use (contracts/registry-runtime.md, ADR-052).

Git is canonical. With `PROMPT_SOURCE=git` (the default, and what production runs) the prompt is the template
file baked into the image: no registry, no network. With `PROMPT_SOURCE=langfuse` a registry version may be
served -- but only if it survives every check below; otherwise the version-controlled copy is served and the
reason is recorded. The registry can therefore never widen what the application does (Principle IV).

Resolution order for a label-resolved prompt (research R-07); the first failing step falls back to Git:

    fetch (never raises)  ->  revoked  ->  not approved (lock)  ->  config allow-list  ->  variable check  ->  serve

`exact`-policy prompts (the eval judges) ALWAYS resolve from Git: registry version numbers differ between
registries, so only the hash is portable (research R-06). Enforcement is always on -- there is no relaxed mode.

Fail-closed rules that hold regardless of source:
* mandatory variables are declared by the CALL SITE in code (`declare_required` / `required_vars`), never by the
  template or the registry (FR-007); code-injected values win over caller values (FR-011);
* a registry version that omits a required variable, references an undeclared one, leaves a declared one without
  a value, or carries a config key other than `temperature`/`response_format` is rejected (FR-009/009a);
* rendering is a single pass; guest text containing `{{...}}` is data, never re-expanded or rejected (FR-009b);
* if the version-controlled copy itself is invalid or revoked, `resolve` RAISES -- that is a build defect that
  `validate_all()` (startup and CI) makes unreachable in a built image (FR-010).
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from prompts.canonical import sha256_of, template_sha256
from prompts.compiler import CompileError, compile_messages
from prompts.langfuse_adapter import AdapterError, LangfuseAdapter, RegistryPrompt
from prompts.lock import LockError, LockState, load_state
from prompts.schema import (
    TEMPLATES_DIR,
    PromptTemplate,
    load_all_templates,
    placeholders_in,
    validate_config,
)
from prompts.tenant import TenantProfile, check_template_tenant_vars

_logger = logging.getLogger(__name__)

CALL_SITE_MODULES: tuple[str, ...] = (
    "agents.dining.nodes.intent_parser",
    "graph.routing.classifier",  # feature 010 — declares intent_routing_classification's required vars
)
"""Modules that declare their mandatory variables via `declare_required` at import. `validate_all(
import_call_sites=True)` imports them so the declarations are visible to a fresh process (`sync validate`)."""


class PromptError(Exception):
    """The version-controlled prompt cannot be served (unknown id, invalid template, or revoked)."""


class RegistryAdapter(Protocol):
    def fetch_prompt(self, name: str, label: str, timeout: int, cache_ttl: int) -> RegistryPrompt | AdapterError: ...


@dataclass(frozen=True)
class RegistrySettings:
    source: str
    label: str
    cache_ttl_seconds: int
    fetch_timeout_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RegistrySettings:
        e = os.environ if env is None else env
        raw_source = e.get("PROMPT_SOURCE", "git").strip().lower() or "git"
        if raw_source not in ("git", "langfuse"):
            _logger.warning("unknown PROMPT_SOURCE %r; using 'git'", raw_source)
            raw_source = "git"
        return cls(
            source=raw_source,
            label=e.get("PROMPT_LABEL", "production").strip() or "production",
            cache_ttl_seconds=_positive_int(e.get("PROMPT_CACHE_TTL_SECONDS"), 60),
            fetch_timeout_seconds=_positive_int(e.get("PROMPT_FETCH_TIMEOUT_SECONDS"), 2),
        )


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        _logger.warning("ignoring non-numeric prompt setting %r; using %d", raw, default)
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class ResolvedPrompt:
    id: str
    version: str
    sha256: str
    source: str
    messages: list[dict[str, str]]
    config: dict[str, Any]
    rejected_reason: str | None = None
    registry_version: int | None = None


# --- call-site declarations -------------------------------------------------------------------------

_REQUIRED: dict[str, frozenset[str]] = {}


def declare_required(prompt_id: str, names: Iterable[str]) -> None:
    """Called by the CALL SITE, in code, to declare the mandatory variables of a prompt (FR-007)."""
    _REQUIRED[prompt_id] = frozenset(names)


def required_names(prompt_id: str) -> frozenset[str]:
    return _REQUIRED.get(prompt_id, frozenset())


# --- process-level caches (local files only; the registry cache belongs to the SDK) --------------------------

_cache_lock = threading.Lock()
_templates_cache: dict[Path, dict[str, PromptTemplate]] = {}
_default_adapter: LangfuseAdapter | None = None
_default_state: LockState | None = None


def reset_caches() -> None:
    """Test/ops hook: forget loaded templates, lock state and the default adapter."""
    global _default_adapter, _default_state
    with _cache_lock:
        _templates_cache.clear()
        _default_adapter = None
        _default_state = None


def _templates(templates_dir: Path | None) -> dict[str, PromptTemplate]:
    directory = (templates_dir or TEMPLATES_DIR).resolve()
    with _cache_lock:
        if directory not in _templates_cache:
            _templates_cache[directory] = load_all_templates(directory)
        return _templates_cache[directory]


def _state() -> LockState:
    global _default_state
    with _cache_lock:
        if _default_state is None:
            try:
                _default_state = load_state()
            except LockError:
                _logger.exception("prompt lock or revocation file is malformed; refusing all registry content")
                _default_state = LockState(broken=True)
        return _default_state


def _adapter() -> LangfuseAdapter:
    global _default_adapter
    with _cache_lock:
        if _default_adapter is None:
            _default_adapter = LangfuseAdapter()
        return _default_adapter


# --- resolution ---------------------------------------------------------------------------------------------


def resolve(
    prompt_id: str,
    *,
    required_vars: Mapping[str, str],
    runtime_vars: Mapping[str, str] | None = None,
    tenant: TenantProfile | None = None,
    settings: RegistrySettings | None = None,
    adapter: RegistryAdapter | None = None,
    state: LockState | None = None,
    templates_dir: Path | None = None,
) -> ResolvedPrompt:
    """Resolve and render a prompt. Never raises for a registry, lock-serving or telemetry fault; raises
    `PromptError` only when the version-controlled copy itself cannot be served."""
    template = _templates(templates_dir).get(prompt_id)
    if template is None:
        raise PromptError(f"unknown prompt id {prompt_id!r}")
    current = settings or RegistrySettings.from_env()
    lock = state if state is not None else _state()

    runtime = dict(runtime_vars or {})
    tenant_vars = tenant.as_variables() if tenant is not None else {}
    declared = set(required_vars) | set(template.variables.runtime) | set(template.variables.tenant)

    git_sha = template_sha256(template)
    if lock.is_revoked(prompt_id, git_sha):
        raise PromptError(f"the version-controlled template {prompt_id!r} is revoked ({git_sha[:12]})")
    try:
        git_messages = compile_messages(
            [{"role": m.role, "content": m.content} for m in template.messages],
            required_vars=required_vars,
            runtime_vars=runtime,
            tenant_vars=tenant_vars,
            declared=declared,
        )
    except CompileError as exc:
        raise PromptError(f"the version-controlled template {prompt_id!r} is invalid: {exc}") from exc

    def from_git(source: str, reason: str | None = None) -> ResolvedPrompt:
        if reason is not None:
            _logger.warning("prompt %s: serving the version-controlled copy (%s, reason=%s)", prompt_id, source, reason)
        return ResolvedPrompt(
            id=prompt_id,
            version=f"git-{git_sha[:12]}",
            sha256=git_sha,
            source=source,
            messages=git_messages,
            config=dict(template.config),
            rejected_reason=reason,
        )

    if current.source != "langfuse" or template.resolution == "exact":
        return from_git("git")

    registry = adapter if adapter is not None else _adapter()
    try:
        fetched = registry.fetch_prompt(
            prompt_id, current.label, current.fetch_timeout_seconds, current.cache_ttl_seconds
        )
    except Exception:  # an adapter bug must not fail a guest turn
        _logger.warning("prompt registry adapter raised for %s", prompt_id, exc_info=True)
        return from_git("git-fallback", "fetch_error")
    if isinstance(fetched, AdapterError):
        return from_git("git-fallback", "wrong_shape" if fetched.reason == "wrong_shape" else "fetch_error")

    sha = sha256_of(fetched.messages, fetched.config)
    if lock.is_revoked(prompt_id, sha):
        return from_git("git-fallback-revoked", "revoked")
    if not lock.is_approved(prompt_id, sha):
        return from_git("git-fallback-unapproved", "unapproved")
    try:
        validate_config(dict(fetched.config))
    except ValueError:
        return from_git("git-fallback", "bad_config")
    try:
        messages = compile_messages(
            fetched.messages,
            required_vars=required_vars,
            runtime_vars=runtime,
            tenant_vars=tenant_vars,
            declared=declared,
        )
    except CompileError as exc:
        return from_git("git-fallback", exc.reason)

    return ResolvedPrompt(
        id=prompt_id,
        version=str(fetched.version),
        sha256=sha,
        source="langfuse",
        messages=messages,
        config=dict(fetched.config),
        registry_version=fetched.version,
    )


# --- startup / CI validation ---------------------------------------------------------------------------------


def validate_all(
    *,
    templates_dir: Path | None = None,
    state: LockState | None = None,
    import_call_sites: bool = False,
) -> None:
    """Fail fast if any version-controlled template is unservable: schema/owner errors, an unmatched or
    undeclared placeholder, a required variable the template never references, a bad tenant variable, or a
    revoked hash. Run at process start (orchestrator, eval runner) and in CI, which is what makes `resolve`'s
    "the Git copy itself is invalid" branch unreachable in a built image (FR-010, FR-014b)."""
    if import_call_sites:
        for module in CALL_SITE_MODULES:
            importlib.import_module(module)
    directory = (templates_dir or TEMPLATES_DIR).resolve()
    templates = load_all_templates(directory)  # raises TemplateError on any schema/owner problem
    lock = state if state is not None else load_state()  # raises LockError on a malformed file

    problems: list[str] = []
    for prompt_id, template in templates.items():
        sha = template_sha256(template)
        if lock.is_revoked(prompt_id, sha):
            problems.append(f"{prompt_id}: the version-controlled template is revoked ({sha[:12]})")
        for name in check_template_tenant_vars(template):
            problems.append(f"{prompt_id}: tenant variable {name!r} is not a TenantProfile field")
        required = required_names(prompt_id)
        used = {name for m in template.messages for name in placeholders_in(m.content)}
        declared = set(required) | set(template.variables.runtime) | set(template.variables.tenant)
        for name in sorted(used - declared):
            problems.append(f"{prompt_id}: placeholder {{{{{name}}}}} is not declared")
        for name in sorted(required - used):
            problems.append(f"{prompt_id}: required variable {{{{{name}}}}} is never referenced by the template")
    if problems:
        raise PromptError("invalid prompt templates:\n  " + "\n  ".join(problems))
