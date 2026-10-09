"""T058 — a failed call's `NormalizedFailure.trace_id` matches the emitted span's own
trace id (FR-021, US8).

`registry.py`'s wrapper reads `span.get_span_context().trace_id` off the very span it
just opened and formats it as the 32-hex-digit W3C trace id before handing it to
`build_normalized_failure`, so the correlation this test proves is exact identity, not
mere presence of both fields.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from gateway._shared import middleware
from gateway._shared.errors import NormalizedFailure, UpstreamFailure
from gateway._shared.middleware import CallerIdentity
from gateway.dining import server as dining_server

from observability import telemetry


@pytest.fixture
def span_exporter():
    exporter = telemetry.use_in_memory_exporter_for_tests()
    yield exporter
    telemetry.reset_for_tests()


@pytest.mark.asyncio
async def test_failure_trace_id_matches_emitted_span(span_exporter, monkeypatch):
    identity = CallerIdentity(
        sub="agent-sub-correlation", tenant_id="MGV", scopes=("dining:hold",), iat=0, exp=9_999_999_999
    )
    token = middleware._current_identity.set(identity)
    monkeypatch.setattr(
        dining_server.dining_client,
        "call",
        AsyncMock(side_effect=UpstreamFailure(error_class="TERMINAL", domain="dining", detail="venue closed")),
    )
    try:
        result = await dining_server.hold_table(
            dining_server.HoldTableInput(venue_id="v1", date="2026-01-01", time="19:00", party_size=2),
            idempotency_key="idem-correlation-1",
        )
    finally:
        middleware._current_identity.reset(token)

    assert isinstance(result, NormalizedFailure)
    assert result.trace_id is not None

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    span_trace_id = f"{span.context.trace_id:032x}"

    assert result.trace_id == span_trace_id


@pytest.mark.asyncio
async def test_two_failed_calls_carry_two_distinct_correlated_trace_ids(span_exporter, monkeypatch):
    """Guards against a stale/reused trace id — each call gets its own span and its own
    correlating trace id, not one fixed at import/registration time."""
    identity = CallerIdentity(
        sub="agent-sub-correlation-2", tenant_id="MGV", scopes=("dining:hold",), iat=0, exp=9_999_999_999
    )
    token = middleware._current_identity.set(identity)
    monkeypatch.setattr(
        dining_server.dining_client,
        "call",
        AsyncMock(side_effect=UpstreamFailure(error_class="TERMINAL", domain="dining", detail="venue closed")),
    )
    try:
        first = await dining_server.hold_table(
            dining_server.HoldTableInput(venue_id="v1", date="2026-01-01", time="19:00", party_size=2),
            idempotency_key="idem-correlation-2",
        )
        second = await dining_server.hold_table(
            dining_server.HoldTableInput(venue_id="v1", date="2026-01-02", time="19:00", party_size=2),
            idempotency_key="idem-correlation-3",
        )
    finally:
        middleware._current_identity.reset(token)

    assert isinstance(first, NormalizedFailure) and isinstance(second, NormalizedFailure)
    assert first.trace_id != second.trace_id

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 2
    span_trace_ids = {f"{s.context.trace_id:032x}" for s in spans}
    assert span_trace_ids == {first.trace_id, second.trace_id}
