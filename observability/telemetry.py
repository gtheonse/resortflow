"""T004 — the shared, cloud-agnostic span-emission module (ADR-041).

The single place any span attribute is set anywhere in this repository
(constitution: "every external call and every graph node MUST emit an
OpenTelemetry span... via the telemetry module. Attribute names MUST NOT be
defined outside that module."). `agents/dining/tracing.py` and
`mcp/gateway/_shared/tracing.py` predate this module and become thin wrappers
over it (T005/T006); every other domain and `graph/supervisor.py` get their
first-ever span emission through it (T007/T008).

Cloud-agnostic by construction (FR-013/014/015, contracts/telemetry-module-
contract.md): the exporter target is read from the standard
`OTEL_EXPORTER_OTLP_ENDPOINT` env var — no ResortFlow-specific variable, so any
OTLP-consuming backend works with zero code change — and `gen_ai.system` is
read from `LLM_PROVIDER` (this build's actual value: `azure-foundry`, see
`.env`/`eval/judge.py`) rather than a hardcoded literal, closing the Tier-1
defect `docs/ResortFlow-Cloud-Portability-Review.md` flagged before it was ever
written into code (ADR-039).
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal, Protocol

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

from observability.attributes import (
    GEN_AI_NAMESPACE,
    LANGFUSE_NAMESPACE,
    PROMPT_NAME,
    PROMPT_REJECTED_REASON,
    PROMPT_SHA256,
    PROMPT_SOURCE,
    PROMPT_VERSION,
    RESORTFLOW_NAMESPACE,
    ROUTING_AMBIGUOUS,
    ROUTING_CARRIED_OVER,
    ROUTING_CONFIDENCE,
    ROUTING_KIND,
    ROUTING_SERVICES,
    ROUTING_TIER,
    ROUTING_TIER2_OUTCOME,
)
from observability.redaction import redact_text

_logger = logging.getLogger(__name__)

LARGE_PAYLOAD_THRESHOLD = 4096
"""chars; below App Insights/most backends' dimension limits. Payloads over
this are truncated with a `resortflow.<kind>.truncated` marker rather than
externalized to blob storage — that fuller design (Deep Dive doc §3) is
deliberately deferred, see plan.md's Complexity Tracking."""

_state: dict[str, Any] = {}


def _build_provider() -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": "resortflow"}))
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        # Imported lazily: environments with no OTLP endpoint configured
        # (e.g. most unit tests) never need this optional dependency path
        # to succeed, matching the graceful-degradation pattern every other
        # optional integration in this repo already follows
        # (agents/dining/tracing.py, agents/dining/nodes/intent_parser.py).
        try:
            import base64

            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
            secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
            if public_key and secret_key:
                # Langfuse's own native OTLP ingestion endpoint (ADR-051,
                # docs/ResortFlow-Observability-Tracing-Deep-Dive.md §6) — NOT the
                # generic /v1/traces suffix. Requires HTTP Basic auth over
                # the project keypair and the ingestion-version header;
                # without the header, directly-ingested data can lag by up
                # to ten minutes per that doc. This branch only ever fires
                # when both Langfuse keys are configured, so a non-Langfuse
                # OTLP backend (e.g. Azure Monitor in production, ADR-041)
                # is unaffected and keeps hitting the generic path below.
                credentials = base64.b64encode(
                    f"{public_key}:{secret_key}".encode()
                ).decode("ascii")
                exporter = OTLPSpanExporter(
                    # `OTLPSpanExporter`'s `endpoint` is used verbatim (it does NOT
                    # auto-append `/v1/traces` the way the env-var-driven default
                    # does) — confirmed live: omitting the suffix here produced a
                    # silent "404 Not Found" on every export (caught only by
                    # actually checking Langfuse's Traces API after a real turn,
                    # not by this exporter raising — see the `except` below).
                    endpoint=f"{endpoint}/api/public/otel/v1/traces",
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "x-langfuse-ingestion-version": "4",
                    },
                )
            else:
                exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:
            # Logged (not raised) so a bad LANGFUSE_*/OTEL_EXPORTER_OTLP_ENDPOINT
            # value is discoverable in process logs instead of silently
            # producing a graph with zero exported spans and no clue why.
            _logger.warning(
                "observability.telemetry: failed to build OTLP exporter for "
                "endpoint=%r; spans will not be exported this run",
                endpoint,
                exc_info=True,
            )
    return provider


def _instrument_openai(provider: TracerProvider) -> None:
    """Feature 008: auto-instruments the raw `openai` SDK client (the only
    chat-completions call shape anywhere in this codebase — no LangChain
    wrapper, confirmed by inspection of `agents/dining/nodes/intent_parser.py`
    and a repo-wide grep for `ChatOpenAI`/`langchain_openai`) so every
    chat-completions/embeddings call emits `gen_ai.request.model` and
    `gen_ai.usage.input_tokens`/`gen_ai.usage.output_tokens` with zero
    instrumentation code at any LLM call site in `agents/`.

    Message/prompt content capture is deliberately left at its default
    (disabled) — this repo already treats free text as something that must be
    redacted before it reaches a span (`observability/redaction.py`), and
    usage/model attributes are emitted unconditionally regardless of content
    capture, per the package's own semantic-convention default.

    Bound to the same `provider` passed to `_tracer()`'s caller so
    auto-instrumented spans nest under whichever `start_agent_span` is
    current — nesting is a property of OTel's ambient context, not of which
    `TracerProvider` object created the span, so this only needs the call
    site to be *inside* a `start_agent_span` block, which every real LLM call
    already is (it happens inside a node function, which `wrap_node`/
    `agents/dining/tracing.py`'s `traced_node` always wrap in a span first)."""
    if _state.get("openai_instrumented"):
        return
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

        OpenAIInstrumentor().instrument(tracer_provider=provider)  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001, S110 — same graceful-degradation posture as the OTLP exporter above
        pass
    else:
        _state["openai_instrumented"] = True


def _tracer() -> trace.Tracer:
    if "test_provider" in _state:
        test_provider: TracerProvider = _state["test_provider"]
        _instrument_openai(test_provider)
        return test_provider.get_tracer("resortflow")
    if "provider" not in _state:
        provider = _build_provider()
        trace.set_tracer_provider(provider)
        _state["provider"] = provider
        _instrument_openai(provider)
    return trace.get_tracer("resortflow")


def get_shared_tracer() -> trace.Tracer:
    """Public accessor for the shared provider's tracer — used by
    `mcp/gateway/_shared/tracing.py`'s thin wrapper (T006) so gateway spans
    land in the same exporter pipeline as agent spans."""
    return _tracer()


def reset_for_tests() -> None:
    """Test-only: force a fresh TracerProvider on next span (env vars may
    have changed between tests, e.g. `OTEL_EXPORTER_OTLP_ENDPOINT`)."""
    _state.pop("provider", None)
    _state.pop("test_provider", None)


def use_in_memory_exporter_for_tests() -> Any:
    """Test-only: build a fresh provider wired to an `InMemorySpanExporter`
    and install it via a module-level override (never touching the process-
    global OTel provider, which the SDK only allows setting once) so tests
    stay isolated from each other and from whatever the real
    `OTEL_EXPORTER_OTLP_ENDPOINT` happens to be. Returns the exporter so a
    test can inspect finished spans."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider(resource=Resource.create({"service.name": "resortflow-test"}))
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _state["test_provider"] = provider
    return exporter


@contextmanager
def start_agent_span(
    name: str, *, node: str, domain: str | None = None, session_id: str | None = None
) -> Iterator[Span]:
    """Single entry point for span creation
    (contracts/telemetry-module-contract.md). Emits `gen_ai.*`, `langfuse.*`,
    and `resortflow.*` together on every call — callers never choose a namespace,
    and never call `span.set_attribute` directly; this is the only module
    that does.

    `session_id`, when provided, is set as `langfuse.session.id` (the exact
    attribute name `observability/attributes.py` already documents but
    nothing previously emitted — ADR-051) so Langfuse can group spans into
    a session/thread view. Keyword-only and optional so every existing
    call site without a session id in scope keeps working unchanged."""
    with _tracer().start_as_current_span(name) as span:
        span.set_attribute(f"{GEN_AI_NAMESPACE}.operation.name", node)
        span.set_attribute(
            f"{GEN_AI_NAMESPACE}.system", os.environ.get("LLM_PROVIDER", "azure-foundry")
        )
        span.set_attribute(f"{LANGFUSE_NAMESPACE}.observation.type", "span")
        span.set_attribute(f"{LANGFUSE_NAMESPACE}.trace.metadata.node", node)
        span.set_attribute(f"{RESORTFLOW_NAMESPACE}.node", node)
        if domain:
            span.set_attribute(f"{RESORTFLOW_NAMESPACE}.domain", domain)
        if session_id:
            span.set_attribute(f"{LANGFUSE_NAMESPACE}.session.id", session_id)
        yield span


class PromptLineage(Protocol):
    """The identity of the prompt a model call used. `prompts.registry.ResolvedPrompt` satisfies it; the
    protocol keeps `observability` free of a dependency on `prompts` (properties, so a frozen dataclass
    matches)."""

    @property
    def id(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def sha256(self) -> str: ...
    @property
    def source(self) -> str: ...
    @property
    def rejected_reason(self) -> str | None: ...


@contextmanager
def start_generation_span(
    name: str,
    *,
    prompt: PromptLineage,
    model: str,
    node: str,
    domain: str | None = None,
    session_id: str | None = None,
) -> Iterator[Span]:
    """A model-call span that records which prompt produced the call (feature 009, ADR-052 D5 option ii).

    Emits everything `start_agent_span` does, marks the observation a `generation`, records the model, and adds
    the five `resortflow.prompt.*` lineage attributes (`rejected_reason` only when a registry version was rejected).
    Prompt TEXT is never an attribute. No `langfuse.observation.prompt.*` attribute is emitted: lineage is
    `resortflow.*` only and nothing here claims the Langfuse UI links a generation to its prompt version. The token
    usage and cost live on the OpenAI-instrumented child span; the two are correlated through this parent."""
    with start_agent_span(name, node=node, domain=domain, session_id=session_id) as span:
        span.set_attribute(f"{LANGFUSE_NAMESPACE}.observation.type", "generation")
        span.set_attribute(f"{GEN_AI_NAMESPACE}.request.model", model)
        span.set_attribute(PROMPT_NAME, prompt.id)
        span.set_attribute(PROMPT_VERSION, prompt.version)
        span.set_attribute(PROMPT_SHA256, prompt.sha256)
        span.set_attribute(PROMPT_SOURCE, prompt.source)
        if prompt.rejected_reason:
            span.set_attribute(PROMPT_REJECTED_REASON, prompt.rejected_reason)
        yield span


def annotate_routing(span: Span, decision: Any) -> None:
    """Feature 010 (ADR-055, FR-029) — sets the seven `resortflow.routing.*`
    attributes on the current `intent_router` span from a routing decision.

    Takes `decision: Any` rather than importing
    `graph.routing.router.RoutingDecision` — `observability/` is a shared
    cross-cutting module that `graph/` depends on, never the reverse
    (AGENTS.md track table), the same reasoning behind `PromptLineage`
    above being a `Protocol` rather than a concrete import. `decision` only
    needs `kind` (`None` or an enum), `services` (an iterable of enums),
    `tier`, `confidence`, `ambiguous`, `carried_over` and `tier2_outcome`
    (an enum).

    Never sets guest message text: `services` is the decision's own
    bounded, code-produced list, not anything copied from the guest's
    message (FR-029)."""
    span.set_attribute(ROUTING_KIND, decision.kind.value if decision.kind is not None else "undecided")
    span.set_attribute(
        ROUTING_SERVICES, ",".join(sorted(s.value for s in decision.services))
    )
    span.set_attribute(ROUTING_TIER, int(decision.tier))
    span.set_attribute(ROUTING_CONFIDENCE, float(decision.confidence))
    span.set_attribute(ROUTING_AMBIGUOUS, bool(decision.ambiguous))
    span.set_attribute(ROUTING_CARRIED_OVER, bool(decision.carried_over))
    span.set_attribute(ROUTING_TIER2_OUTCOME, decision.tier2_outcome.value)


def wrap_node(
    node_name: str, fn: Callable[[dict[str, Any]], Any], *, domain: str
) -> Callable[[dict[str, Any]], Any]:
    """T007 — generic node-wrapping helper for domains that had no tracer at
    all (`entertainment`, `stay`, `wellness`, `gaming`; `dining` keeps its
    own thin `traced_node` for backward-compatible call-site parity, T005).
    Mirrors `agents/dining/tracing.py`'s shape exactly, parameterized by
    domain, closing the constitution's previously-unmet "every node emits"
    requirement outside dining."""
    is_async = inspect.iscoroutinefunction(fn)

    if is_async:

        async def async_wrapper(state: dict[str, Any]) -> Any:
            with start_agent_span(
                node_name, node=node_name, domain=domain, session_id=state.get("session_id")
            ) as span:
                result = await fn(state)
                set_payload(span, "output", str(result))
                return result

        return async_wrapper

    def sync_wrapper(state: dict[str, Any]) -> Any:
        with start_agent_span(
            node_name, node=node_name, domain=domain, session_id=state.get("session_id")
        ) as span:
            result = fn(state)
            set_payload(span, "output", str(result))
            return result

    return sync_wrapper


def set_payload(span: Span, kind: Literal["input", "output", "context"], value: str) -> None:
    """Applies PII masking (`observability.redaction.redact_text`) before
    setting any attribute. Oversized payloads are truncated with a
    `resortflow.<kind>.truncated` marker."""
    masked = redact_text(value) if value else value
    truncated = bool(masked and len(masked) > LARGE_PAYLOAD_THRESHOLD)
    if truncated:
        masked = (masked or "")[:LARGE_PAYLOAD_THRESHOLD]
    gen_ai_key = {"input": "prompt", "output": "completion", "context": "context"}[kind]
    span.set_attribute(f"{GEN_AI_NAMESPACE}.{gen_ai_key}", masked or "")
    span.set_attribute(f"{LANGFUSE_NAMESPACE}.observation.{kind}", masked or "")
    span.set_attribute(f"{RESORTFLOW_NAMESPACE}.{kind}.truncated", truncated)
