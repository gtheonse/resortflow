"""T006 — thin wrapper over `observability.telemetry` (ADR-041).

Was a standalone bare-OTel tracer (ADR-016) with its own `TracerProvider`;
now every span the gateway emits shares the one provider
`observability.telemetry` builds, so gateway spans and agent spans land in
the same exporter pipeline. Public API (`initialize_tracer`, `get_tracer`)
is unchanged — `mcp/gateway/main.py` and `mcp/gateway/_shared/registry.py`
need no edit.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace

from observability.telemetry import get_shared_tracer


def initialize_tracer(*, use_console_exporter: bool = False) -> None:
    """Called once at gateway startup. `use_console_exporter` is retained
    for signature compatibility with existing call sites
    (`mcp/gateway/main.py`, `agents/tests/test_tenant_isolation.py`); the
    shared provider's exporter selection is now config-driven
    (`OTEL_EXPORTER_OTLP_ENDPOINT`) rather than a constructor argument."""
    get_shared_tracer()


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer for a module, sourced from the shared provider
    `observability.telemetry` owns."""
    return get_shared_tracer()


def get_emitted_spans() -> list[Any]:
    """No longer backed by a gateway-local in-memory exporter (the shared
    provider's exporter is config-driven, not always in-memory) — no
    call site in this repository uses this beyond its own former tests."""
    return []
