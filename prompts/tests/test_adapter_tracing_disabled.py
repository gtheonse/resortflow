"""T026 -- FR-031 (non-blocking, no server): a tracing-disabled registry client is inert.

The Langfuse SDK, constructed normally, starts its own tracer provider and attaches a span processor, which
would double-export the spans ADR-051's exporter already sends. `prompts/langfuse_adapter.py` therefore builds
the client with `tracing_enabled=False`. This test uses the REAL SDK (no network needed: the host is
unreachable and nothing is fetched) and proves the project's own exporter stays the only exporter.

Test-design note (ADR-053 "one client per process"): the SDK keeps ONE resource manager per public key, so a
client shut down by one test would be handed back, already dead, to the next test that reuses the key -- and a
second `shutdown()` on it blocks. Every test here therefore uses its own public key and shuts its client down
exactly once, in a bounded daemon thread so a misbehaving SDK can never hang the suite.
"""

from __future__ import annotations

import threading
from typing import Any

from opentelemetry import trace

from observability import telemetry
from prompts.langfuse_adapter import LangfuseAdapter


def _env(tag: str) -> dict[str, str]:
    return {
        "LANGFUSE_PUBLIC_KEY": f"pk-lf-test-{tag}",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_HOST": "http://127.0.0.1:9",  # nothing listens here
    }


def _processor_count(provider: Any) -> int:
    active = getattr(provider, "_active_span_processor", None)
    processors = getattr(active, "_span_processors", ())
    return len(processors)


def _shutdown_once(client: Any) -> None:
    shutdown = getattr(client, "shutdown", None)
    if not callable(shutdown):
        return
    worker = threading.Thread(target=shutdown, daemon=True)
    worker.start()
    worker.join(timeout=10)


def test_client_construction_does_not_touch_the_global_tracer_provider() -> None:
    before = trace.get_tracer_provider()
    processors_before = _processor_count(before)

    client = LangfuseAdapter(env=_env("provider")).client()
    try:
        assert client is not None  # the real SDK client was constructed
        after = trace.get_tracer_provider()
        assert after is before  # not replaced
        assert _processor_count(after) == processors_before  # no span processor attached
    finally:
        _shutdown_once(client)


def test_exported_span_set_is_unchanged_by_the_client() -> None:
    exporter = telemetry.use_in_memory_exporter_for_tests()
    client = None
    try:
        client = LangfuseAdapter(env=_env("spans")).client()
        with telemetry.start_agent_span("only-span", node="n"):
            pass
        assert [span.name for span in exporter.get_finished_spans()] == ["only-span"]  # the SDK added nothing
    finally:
        _shutdown_once(client)
        telemetry.reset_for_tests()


def test_client_is_constructed_lazily_and_once() -> None:
    adapter = LangfuseAdapter(env=_env("once"))
    first = adapter.client()
    try:
        assert first is not None
        assert adapter.client() is first
    finally:
        _shutdown_once(first)
