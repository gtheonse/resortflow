"""Feature 008: the actual verification logic for
`test_telemetry_openai_instrumentation.py`, run in its own subprocess (not
imported/executed directly by pytest — hence the leading underscore, which
keeps pytest from collecting it as a test module).

Why a subprocess: `opentelemetry.instrumentation.openai_v2.OpenAIInstrumentor`
binds its patched `chat.completions.create` to whichever `TracerProvider` is
passed to `.instrument()` the first time it succeeds, for the life of the
process (`BaseInstrumentor` tracks `_is_instrumented_by_opentelemetry` on the
instance, and `observability.telemetry._instrument_openai` only ever calls it
once per process via its own `_state["openai_instrumented"]` guard — see that
module's docstring). If any other test earlier in the same pytest session
triggers `observability.telemetry._tracer()` first, this module's own
`use_in_memory_exporter_for_tests()` swap would be silently ignored for
auto-instrumented spans specifically, producing a false failure that depends
on test execution order rather than on anything actually being wrong. A fresh
subprocess sidesteps that global-binding constraint entirely.

Exits 0 with "OK" on success; exits 1 with a diagnostic on any failure.
"""
from __future__ import annotations

import sys

import httpx
from openai import AzureOpenAI

from observability.telemetry import (
    reset_for_tests,
    start_agent_span,
    use_in_memory_exporter_for_tests,
)

_CANNED_RESPONSE = {
    "id": "chatcmpl-verify",
    "object": "chat.completion",
    "created": 1234567890,
    "model": "gpt-4.1-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": '{"party_size": 2}'},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168},
}


def _mock_transport(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_CANNED_RESPONSE)


def main() -> int:
    reset_for_tests()
    exporter = use_in_memory_exporter_for_tests()

    mock_client = httpx.Client(transport=httpx.MockTransport(_mock_transport))
    azure_client = AzureOpenAI(  # type: ignore[call-overload]
        azure_endpoint="https://verify.example.com",
        api_key="verify-key",
        api_version="2024-10-21",
        http_client=mock_client,
    )

    with start_agent_span("dining.intent_parser", node="dining.intent_parser", domain="dining") as node_span:
        response = azure_client.chat.completions.create(
            model="gpt-4.1-mini-deployment",
            messages=[{"role": "user", "content": "table for 2 tonight"}],
        )

    if response.choices[0].message.content != '{"party_size": 2}':
        print("FAIL: mocked response content not honored")
        return 1

    spans = exporter.get_finished_spans()
    node_spans = [s for s in spans if s.name == "dining.intent_parser"]
    llm_spans = [s for s in spans if s.name != "dining.intent_parser"]

    if len(node_spans) != 1:
        print(f"FAIL: expected exactly 1 node span, got {len(node_spans)}")
        return 1
    if len(llm_spans) != 1:
        print(f"FAIL: expected exactly 1 auto-instrumented LLM span, got {len(llm_spans)}")
        return 1

    node_span_context = node_span.get_span_context()
    llm_span = llm_spans[0]
    attrs = llm_span.attributes or {}

    checks = {
        "gen_ai.usage.input_tokens": (attrs.get("gen_ai.usage.input_tokens"), 123),
        "gen_ai.usage.output_tokens": (attrs.get("gen_ai.usage.output_tokens"), 45),
        "gen_ai.request.model": (attrs.get("gen_ai.request.model"), "gpt-4.1-mini-deployment"),
    }
    ok = True
    for key, (actual, expected) in checks.items():
        if actual != expected:
            print(f"FAIL: {key} expected {expected!r}, got {actual!r}")
            ok = False

    if llm_span.parent is None or llm_span.parent.span_id != node_span_context.span_id:
        print(
            f"FAIL: auto-instrumented span not nested under node span "
            f"(parent={llm_span.parent}, node_span_id={node_span_context.span_id:x})"
        )
        ok = False

    if llm_span.context.trace_id != node_span_context.trace_id:
        print("FAIL: auto-instrumented span is not in the same trace as the node span")
        ok = False

    if not ok:
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
