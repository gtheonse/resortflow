"""Feature 010, T040 — tier 2: the constrained economy-tier routing
classifier (research R-07, ADR-055,
`specs/010-tiered-intent-routing/contracts/router-api.md`).

Declares `intent_routing_classification`'s required variables at import
time (`declare_required`, same top-level pattern as
`agents/dining/nodes/intent_parser.py`'s `DINING_INTENT_PROMPT_ID`), so
`prompts.registry.validate_all()` (run at process start by `graph/api.py`,
and by `python -m prompts.sync validate` in CI) never flags
`{{allowed_kinds}}`/`{{allowed_services}}` as undeclared placeholders.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from graph.models import Domain, RequestKind, Tier2Outcome
from prompts.registry import declare_required, resolve

INTENT_ROUTING_PROMPT_ID = "intent_routing_classification"

declare_required(INTENT_ROUTING_PROMPT_ID, ["allowed_kinds", "allowed_services"])

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 1.5
_MAX_SERVICES = 5


class ClassifierOutput(BaseModel):
    """The ONLY shape a tier-2 response may take (FR-010). `extra="forbid"`
    rejects a `tenant_id`/`guest_id` key outright — FR-012's guarantee
    that tenant/guest identity is never derived from the classifier is
    enforced by this type having no such field, not by a downstream
    filter that could be forgotten (analyze L3)."""

    model_config = ConfigDict(extra="forbid")

    kind: RequestKind
    services: list[Domain] = []

    @field_validator("services")
    @classmethod
    def _bounded_and_unique(cls, value: list[Domain]) -> list[Domain]:
        if len(value) > _MAX_SERVICES:
            raise ValueError(f"services exceeds the {_MAX_SERVICES}-item bound")
        if len(set(value)) != len(value):
            raise ValueError("services must be unique")
        return value


class ClassifierResult(BaseModel):
    """What `route()` receives back from any `RoutingClassifier` — either
    a valid `output`, or `None` with a `Tier2Outcome` explaining why."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    output: ClassifierOutput | None
    outcome: Tier2Outcome


class PreviousTurnContext(Protocol):
    """Structural — `graph.routing.kinds.PreviousTurn` satisfies it."""

    kind: RequestKind | None
    services: list[Domain]


class RoutingClassifier(Protocol):
    async def classify(self, text: str, *, previous: PreviousTurnContext | None) -> ClassifierResult: ...


def _previous_context_string(previous: PreviousTurnContext | None) -> str:
    if previous is None or previous.kind is None:
        return "none"
    services = ", ".join(s.value for s in previous.services)
    return f"kind={previous.kind.value}; services={services}"


class UnavailableClassifier:
    """The default when no Azure OpenAI credentials are configured —
    every call reports `unavailable`, never raises, and the caller (the
    router) degrades to the existing clarifying question (SC-006)."""

    async def classify(self, text: str, *, previous: PreviousTurnContext | None = None) -> ClassifierResult:
        return ClassifierResult(output=None, outcome=Tier2Outcome.UNAVAILABLE)


class AzureRoutingClassifier:
    """Economy runtime tier (`MODEL_CHEAP`), declared statically
    (Constitution Principle VI) — no escalation on failure: a 1.5 s
    timeout and no retry, because a fallback to the clarifying question
    costs far less than a second frontier-tier call (research R-07)."""

    def __init__(self, *, endpoint: str, api_key: str, api_version: str, deployment: str) -> None:
        from openai import AsyncAzureOpenAI  # imported lazily — optional dependency path

        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint, api_key=api_key, api_version=api_version, timeout=_TIMEOUT_SECONDS
        )
        self._deployment = deployment

    async def classify(self, text: str, *, previous: PreviousTurnContext | None = None) -> ClassifierResult:
        from observability.telemetry import start_generation_span

        resolved = resolve(
            INTENT_ROUTING_PROMPT_ID,
            required_vars={
                "allowed_kinds": ", ".join(k.value for k in RequestKind),
                "allowed_services": ", ".join(d.value for d in Domain),
            },
            runtime_vars={
                "guest_message": text,
                "previous_context": _previous_context_string(previous),
            },
        )
        try:
            with start_generation_span(
                "routing.intent_classification",
                prompt=resolved,
                model=self._deployment,
                node="intent_router",
                domain="supervisor",
            ):
                response = await self._client.chat.completions.create(
                    model=self._deployment,
                    # `resolved.messages` is `list[dict[str, str]]` (the
                    # registry's own resolved-prompt shape); the openai SDK's
                    # stub wants its own per-role TypedDict union — the
                    # dicts satisfy it structurally at runtime (same
                    # untyped-dict call agents/dining/nodes/intent_parser.py
                    # already makes; that module just isn't under mypy
                    # --strict yet).
                    messages=resolved.messages,  # type: ignore[arg-type]
                    **resolved.config,
                )
        except TimeoutError:
            return ClassifierResult(output=None, outcome=Tier2Outcome.TIMEOUT)
        except Exception:
            logger.warning("routing classifier call failed", exc_info=True)
            return ClassifierResult(output=None, outcome=Tier2Outcome.UNAVAILABLE)

        raw = response.choices[0].message.content or ""
        try:
            payload = json.loads(raw)
            output = ClassifierOutput.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return ClassifierResult(output=None, outcome=Tier2Outcome.INVALID)
        return ClassifierResult(output=output, outcome=Tier2Outcome.DECIDED)


def build_default_classifier() -> RoutingClassifier:
    """Azure OpenAI when configured, `UnavailableClassifier` otherwise —
    the same credential-fallback pattern
    `agents.dining.nodes.intent_parser.build_default_extractor` uses."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    if endpoint and api_key:
        try:
            return AzureRoutingClassifier(
                endpoint=endpoint,
                api_key=api_key,
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                deployment=os.environ.get("MODEL_CHEAP", "gpt-4.1-mini"),
            )
        except Exception:  # noqa: BLE001 — a broken optional dependency/misconfig must degrade
            return UnavailableClassifier()
    return UnavailableClassifier()
