"""T011/T050a — the pinned, provider-agnostic judge-model client (FR-009, FR-013a, SC-002,
ADR-044 amended, ADR-052/053).

Mirrors `agents/dining/nodes/intent_parser.py`'s Protocol + factory +
graceful-degradation shape, with one deliberate difference: there is no
"rule-based fallback" for a judge score the way there is for intent
parsing — a qualitative judgment either comes from the pinned LLM or it
doesn't exist. When no provider is configured, `build_default_judge_client()`
returns a client whose `.complete()` raises `JudgeUnavailableError` rather
than fabricating a score. `eval/runner.py`'s FR-021/022 retry-then-INCOMPLETE
handling (T026) is the intended catcher for that error — a missing judge
produces an honest `INCOMPLETE` verdict, never a silent `PASS`.

This build's actual, working endpoint is **Azure AI Foundry**
(`FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_JUDGE_DEPLOYMENT_NAME=gpt-5-mini` — see
`.env`), not a raw Azure OpenAI resource (`AZURE_OPENAI_ENDPOINT` ships
blank). Both are OpenAI-compatible endpoints from the client's point of
view, so one client class (`OpenAICompatibleJudgeClient`) serves either —
provider selection is which environment variables happen to be populated,
never a code branch (FR-013a).

T050a: `complete()` now takes `prompt: ResolvedPrompt` as a REQUIRED keyword argument — not
optional, not defaulted — so a future call site cannot silently skip prompt lineage (SC-002 requires
100% of in-scope model calls to carry it). The real client wraps its network call in
`observability.telemetry.start_generation_span`; the fake and unconfigured clients accept and ignore
it, since prompt identity is meaningless when no real call is made.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from prompts.registry import ResolvedPrompt


class JudgeUnavailableError(RuntimeError):
    """Raised when no judge provider is configured, or the configured one
    fails — never caught here to produce a fabricated score. FR-021/FR-022
    (T026) turns this into a bounded retry, then `NOT_EVALUATED`/
    `INCOMPLETE`, never a silent `PASS`."""


class JudgeClient(Protocol):
    def complete(self, *, system: str, user: str, prompt: ResolvedPrompt) -> str: ...


class FakeJudgeClient:
    """Deterministic, canned-response client — the ONLY client any
    automated test in this repository should use (mirrors every existing
    eval runner's "fake tools" pattern, e.g. `agents/dining/eval/runner.py`).
    Never makes a network call, so the test suite needs no live provider
    credentials at all. Accepts and ignores `prompt` — no span is emitted."""

    def __init__(self, response: str = '{"score": 1.0, "reasoning": "fake"}') -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str, prompt: ResolvedPrompt) -> str:
        self.calls.append((system, user))
        return self.response


class OpenAICompatibleJudgeClient:
    """A minimal OpenAI-compatible chat-completions client. Provider,
    endpoint, authentication, and the pinned deployment are all constructor
    arguments — never hardcoded — so swapping providers is a configuration
    change to `build_default_judge_client()`'s env-var resolution, not a
    code change here or in any scorer that calls this class (FR-013a,
    SC-017)."""

    def __init__(self, *, endpoint: str, api_key: str, api_version: str, deployment: str) -> None:
        from openai import AzureOpenAI  # imported lazily — optional dependency path

        self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
        self._deployment = deployment

    def complete(self, *, system: str, user: str, prompt: ResolvedPrompt) -> str:
        from observability.telemetry import start_generation_span

        try:
            with start_generation_span(
                f"eval.judge.{prompt.id}", prompt=prompt, model=self._deployment,
                node="eval_judge", domain="eval",
            ):
                response = self._client.chat.completions.create(
                    model=self._deployment,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0,
                )
        except Exception as exc:
            raise JudgeUnavailableError(str(exc)) from exc
        return response.choices[0].message.content or ""


class _UnconfiguredJudgeClient:
    """Never fabricates a score (see module docstring)."""

    def complete(self, *, system: str, user: str, prompt: ResolvedPrompt) -> str:
        raise JudgeUnavailableError(
            "no judge provider configured — set FOUNDRY_PROJECT_ENDPOINT+AZURE_OPENAI_API_KEY "
            "or AZURE_OPENAI_ENDPOINT+AZURE_OPENAI_API_KEY"
        )


def pinned_model_id() -> str:
    """The judge's pinned deployment identifier, named in every report
    (FR-009) — never `latest`. Prefers the Foundry-specific name (already
    reserved for the judge in `.env`), falling back to the generic
    `MODEL_JUDGE` env var used by a plain Azure OpenAI deployment."""
    return os.environ.get("FOUNDRY_JUDGE_DEPLOYMENT_NAME") or os.environ.get(
        "MODEL_JUDGE", "gpt-4.1"
    )


def provider_name() -> str:
    """Matches `observability.telemetry`'s `gen_ai.system` default —
    kept as a single source (`LLM_PROVIDER`) so the report's judge_model
    provider and every span's `gen_ai.system` never disagree."""
    return os.environ.get("LLM_PROVIDER", "azure-foundry")


def build_default_judge_client() -> JudgeClient:
    """Foundry when configured (this build's actual, working provider — see
    module docstring), plain Azure OpenAI when only that's configured,
    else a client that raises `JudgeUnavailableError` on first use rather
    than ever fabricating a score."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    foundry_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

    endpoint = foundry_endpoint or azure_endpoint
    if endpoint and api_key:
        try:
            return OpenAICompatibleJudgeClient(
                endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
                deployment=pinned_model_id(),
            )
        except Exception:  # noqa: BLE001 — optional-dependency/misconfig must degrade, never crash
            return _UnconfiguredJudgeClient()
    return _UnconfiguredJudgeClient()
