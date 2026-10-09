"""T056/T057/T059 — tracing tests for the MCP gateway (US8, ADR-016/ADR-041).

Uses `observability.telemetry`'s in-memory exporter (the shared provider every
gateway span now flows through, per `mcp/gateway/_shared/tracing.py`'s thin-wrapper
docstring) rather than a gateway-local tracer, since the bare-tracer-with-its-own-
provider design (ADR-016) was superseded by ADR-041's shared provider.

T056: exactly one span per tool invocation, success and failure, carrying tool,
domain, tenant, session, idempotency key (where applicable), and outcome (SC-012).

T057: no attribute-name string literal appears anywhere under `mcp/gateway/`
outside a name imported from `observability/attributes.py` (FR-028), enforced by
AST inspection of every module under that package.

T059: no guest-identifying value reaches an emitted span (FR-029, SC-013).
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import AsyncMock

import pytest
from gateway._shared import middleware
from gateway._shared.errors import NormalizedFailure, UpstreamFailure
from gateway._shared.middleware import CallerIdentity
from gateway.dining import server as dining_server
from gateway.dining.models import Hold, VenuesResponse
from gateway.gaming import server as gaming_server
from gateway.gaming.models import ResponsibleGamingStatus

from observability import telemetry
from observability.attributes import (
    IDEMPOTENCY_KEY,
    OUTCOME,
    RESORTFLOW_NAMESPACE,
    SESSION_ID,
    TENANT_ID,
)

GATEWAY_ROOT = pathlib.Path(__file__).resolve().parents[2] / "gateway"


def _identity(*, sub: str = "test-sub", tenant_id: str = "MGV", scopes: tuple[str, ...] = ()) -> CallerIdentity:
    return CallerIdentity(sub=sub, tenant_id=tenant_id, scopes=scopes, iat=0, exp=9_999_999_999)


@pytest.fixture
def span_exporter():
    """Installs a fresh in-memory-exporter-backed provider (`observability.telemetry`)
    for the duration of one test, then tears it down so tests stay isolated from each
    other and from any real `OTEL_EXPORTER_OTLP_ENDPOINT`."""
    exporter = telemetry.use_in_memory_exporter_for_tests()
    yield exporter
    telemetry.reset_for_tests()


def _set_identity(identity: CallerIdentity):
    return middleware._current_identity.set(identity)


def _reset_identity(token) -> None:
    middleware._current_identity.reset(token)


# --- T056: span-emission -----------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_one_span_on_success(span_exporter, monkeypatch):
    identity = _identity(sub="agent-sub-1", tenant_id="MGV", scopes=("dining:read",))
    token = _set_identity(identity)
    monkeypatch.setattr(
        dining_server.dining_client, "call", AsyncMock(return_value=VenuesResponse(venues=[]))
    )
    try:
        result = await dining_server.list_venues(dining_server.ListVenuesInput())
    finally:
        _reset_identity(token)

    assert isinstance(result, VenuesResponse)

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1, f"expected exactly one span, got {len(spans)}"
    attrs = spans[0].attributes

    assert attrs[f"{RESORTFLOW_NAMESPACE}.node"] == "resortflow.dining.list_venues"
    assert attrs[f"{RESORTFLOW_NAMESPACE}.domain"] == "dining"
    assert attrs[TENANT_ID] == "MGV"
    assert attrs[SESSION_ID] == "agent-sub-1"
    assert attrs[OUTCOME] == "success"
    # A read tool declares no idempotency key at all.
    assert IDEMPOTENCY_KEY not in attrs


@pytest.mark.asyncio
async def test_exactly_one_span_on_failure_carries_idempotency_key(span_exporter, monkeypatch):
    identity = _identity(sub="agent-sub-2", tenant_id="MHB", scopes=("dining:hold",))
    token = _set_identity(identity)
    monkeypatch.setattr(
        dining_server.dining_client,
        "call",
        AsyncMock(side_effect=UpstreamFailure(error_class="TERMINAL", domain="dining", detail="venue closed")),
    )
    try:
        result = await dining_server.hold_table(
            dining_server.HoldTableInput(venue_id="v1", date="2026-01-01", time="19:00", party_size=2),
            idempotency_key="idem-key-123",
        )
    finally:
        _reset_identity(token)

    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "TERMINAL"

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1, f"expected exactly one span, got {len(spans)}"
    attrs = spans[0].attributes

    assert attrs[f"{RESORTFLOW_NAMESPACE}.node"] == "resortflow.dining.hold_table"
    assert attrs[f"{RESORTFLOW_NAMESPACE}.domain"] == "dining"
    assert attrs[TENANT_ID] == "MHB"
    assert attrs[SESSION_ID] == "agent-sub-2"
    assert attrs[OUTCOME] == "TERMINAL"
    assert attrs[IDEMPOTENCY_KEY] == "idem-key-123"


# --- T057: vocabulary closure --------------------------------------------------------


def _imported_from_attributes(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "observability.attributes":
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _set_attribute_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_attribute"
        and node.args
    ]


def test_no_attribute_name_literals_outside_observability_attributes():
    """FR-028: every attribute-name string used anywhere under `mcp/gateway/` must
    trace back to a name imported from `observability/attributes.py` — never a bare
    string literal typed at the call site."""
    violations: list[str] = []

    for path in sorted(GATEWAY_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported = _imported_from_attributes(tree)

        for call in _set_attribute_calls(tree):
            first = call.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                violations.append(
                    f"{path}:{first.lineno}: hardcoded attribute-name literal {first.value!r}"
                )
            elif isinstance(first, ast.Name):
                if first.id not in imported:
                    violations.append(
                        f"{path}:{first.lineno}: attribute name {first.id!r} is not imported "
                        "from observability.attributes"
                    )
            elif isinstance(first, ast.JoinedStr):
                for value in first.values:
                    is_name_ref = isinstance(value, ast.FormattedValue) and isinstance(
                        value.value, ast.Name
                    )
                    if is_name_ref and value.value.id not in imported:
                        violations.append(
                            f"{path}:{first.lineno}: f-string attribute name references "
                            f"{value.value.id!r}, not imported from observability.attributes"
                        )
            else:
                violations.append(
                    f"{path}:{first.lineno}: attribute-name argument is neither a literal, a "
                    "plain imported name, nor an f-string of imported names — cannot verify "
                    "provenance"
                )

    assert not violations, "attribute-name vocabulary closure violated:\n" + "\n".join(violations)


# --- T059: PII absence ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_guest_identifying_value_reaches_a_span(span_exporter, monkeypatch):
    """FR-029/SC-013: a guest identifier passed as a tool argument must never show up
    as (or inside) any emitted span attribute value."""
    guest_id = "guest-super-secret-42"
    identity = _identity(sub="agent-sub-3", tenant_id="MGV", scopes=("gaming:read",))
    token = _set_identity(identity)
    monkeypatch.setattr(
        gaming_server.gaming_client,
        "call",
        AsyncMock(
            return_value=ResponsibleGamingStatus(
                guest_id=guest_id,
                jurisdiction="NGCB",
                self_excluded=False,
                limit_flags=[],
                offers_permitted=True,
                gaming_content_permitted=True,
                support_resources=[],
            )
        ),
    )
    try:
        await gaming_server.check_rg_status(gaming_server.CheckRgStatusInput(guest_id=guest_id))
    finally:
        _reset_identity(token)

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    for key, value in spans[0].attributes.items():
        assert guest_id not in str(value), f"guest id leaked into span attribute {key}={value!r}"


@pytest.mark.asyncio
async def test_hold_table_arguments_do_not_leak_into_span(span_exporter, monkeypatch):
    """A mutating call's booking details (venue/date/time/party size) are business
    data, not part of the frozen telemetry vocabulary, and must not appear on the span
    either — the span identifies the call, not its payload."""
    identity = _identity(sub="agent-sub-4", tenant_id="MGV", scopes=("dining:hold",))
    token = _set_identity(identity)
    monkeypatch.setattr(
        dining_server.dining_client,
        "call",
        AsyncMock(
            return_value=Hold(
                hold_id="hold-1",
                venue_id="venue-secret-guest-linked",
                date="2026-01-01",
                time="19:00",
                party_size=2,
                status="held",
                expires_at="2026-01-01T19:15:00Z",
            )
        ),
    )
    try:
        await dining_server.hold_table(
            dining_server.HoldTableInput(
                venue_id="venue-secret-guest-linked", date="2026-01-01", time="19:00", party_size=2
            ),
            idempotency_key="idem-key-456",
        )
    finally:
        _reset_identity(token)

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    for key, value in spans[0].attributes.items():
        assert "venue-secret-guest-linked" not in str(value), (
            f"booking payload leaked into span attribute {key}={value!r}"
        )
