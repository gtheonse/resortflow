"""T067 [CRIT] — cross-tenant isolation for entertainment (the one wholly
new gateway server this feature adds): proves `tenant_id` injection at the
gateway boundary (`mcp/gateway/entertainment/server.py`, T023) is impossible
to bypass, per Constitution Principle II ("proven by test rather than
asserted"). Exercises the REAL MCP wire protocol (real JSON-RPC over a real
`streamable_http_app()`, in-process via an ASGI transport rather than a live
socket) — not a mocked agent-level abstraction — because the boundary this
feature adds is exactly that wire hop (ADR-021).

Each test builds its own fresh `streamable_http_app()` + minimal FastAPI
wrapper (mirroring `gateway.main`'s own auth-middleware + mount shape)
rather than sharing `gateway.main`'s module-level singleton apps:
`StreamableHTTPSessionManager.run()` may only be entered once per instance,
and pytest-asyncio's default function-scoped event loop makes a
module-scoped shared instance's lifespan teardown fight over which loop owns
its anyio task group. A fresh app per test sidesteps both at once, at the
cost of one extra `MCPServer.streamable_http_app()` call per test — cheap,
since that call only wraps the same underlying tool registry.

No live `sims/ticketing` process exists in this test environment, so the
one mock in this file is `entertainment_client.call` (the `UpstreamClient` a
tool body would otherwise use to reach the vendor) — everything ahead of
that call (bearer token verification, `AuthenticationMiddleware`,
`CallerIdentity` propagation, the real MCP session handshake and tool-call
dispatch) is exercised for real.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from fastapi import FastAPI
from gateway._shared.identity import issue_token
from gateway._shared.middleware import AuthenticationMiddleware
from gateway._shared.tracing import initialize_tracer
from gateway.entertainment.models import SearchEventsResponse
from gateway.entertainment.server import app as entertainment_mcp
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings

from mcp import ClientSession

_TRANSPORT_SECURITY = TransportSecuritySettings(allowed_hosts=["testserver"])


def _build_test_app() -> FastAPI:
    """A minimal standalone gateway, mirroring `mcp/gateway/main.py`'s real
    shape (auth middleware ahead of the mounted domain app) but scoped to
    just entertainment and built fresh per test."""
    entertainment_asgi = entertainment_mcp.streamable_http_app(
        stateless_http=True, transport_security=_TRANSPORT_SECURITY
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize_tracer(use_console_exporter=False)
        async with entertainment_asgi.router.lifespan_context(entertainment_asgi):
            yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(AuthenticationMiddleware)
    app.mount("/entertainment", entertainment_asgi)
    return app


async def _call_search_events(
    tenant_id: str, *, extra_body_fields: dict | None = None
) -> str | None:
    """Real MCP session over a real ASGI transport (no live server socket),
    against a real gateway app with real auth middleware and real tool
    dispatch. Returns the `tenant_id` the mocked upstream call actually
    received, or `None` if the gateway rejected the request before any
    upstream call was made."""
    app = _build_test_app()
    token = issue_token(sub="g1", tenant_id=tenant_id, scopes=["entertainment:read"])
    transport = httpx2.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}", "Host": "testserver"}

    captured: dict = {}

    async def _fake_upstream_call(method, path, *, tenant_id, response_model, **kwargs):
        captured["tenant_id"] = tenant_id
        return SearchEventsResponse(events=[], next_page_token=None)

    async with app.router.lifespan_context(app):
        with patch(
            "gateway.entertainment.server.entertainment_client.call",
            new=AsyncMock(side_effect=_fake_upstream_call),
        ):
            async with httpx2.AsyncClient(
                transport=transport, headers=headers, base_url="http://testserver"
            ) as http_client:
                async with streamable_http_client(
                    "http://testserver/entertainment/mcp", http_client=http_client
                ) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        # The real MCP tool schema wraps the tool's own
                        # fields under "input" (`graph/mcp_client.py`'s
                        # exact wrapping — mirrored here since this test
                        # drives the raw mcp ClientSession directly, not
                        # through McpClient).
                        tool_input = {"classification": "music"}
                        if extra_body_fields:
                            tool_input.update(extra_body_fields)
                        await session.call_tool(
                            "resortflow.entertainment.search_events", arguments={"input": tool_input}
                        )

    return captured.get("tenant_id")


@pytest.mark.asyncio
async def test_mgv_session_calls_upstream_with_mgv_tenant():
    tenant_id = await _call_search_events("MGV")
    assert tenant_id == "MGV"


@pytest.mark.asyncio
async def test_mhb_session_calls_upstream_with_mhb_tenant():
    tenant_id = await _call_search_events("MHB")
    assert tenant_id == "MHB"


@pytest.mark.asyncio
async def test_no_client_supplied_field_can_override_the_verified_tenant():
    """Even a caller stuffing a `tenant_id`-shaped field into the tool
    arguments cannot influence which tenant the upstream call scopes to —
    `SearchEventsInput`'s Pydantic model (every gateway input model, per
    `mcp-tool-contract.md`) rejects an unrecognized field outright, and even
    if it didn't, the tool body never reads `tenant_id` from anywhere but
    `get_current_identity()`."""
    # An MHB-authenticated session attempting to claim MGV via a bogus field.
    tenant_id = await _call_search_events("MHB", extra_body_fields={"tenant_id": "MGV"})
    # The gateway rejects the unrecognized field, so the upstream is normally never called
    # (`None`). If it were called, it must carry the verified token's tenant — never the
    # client-supplied one.
    assert tenant_id in (None, "MHB"), "a client-supplied tenant_id must never reach the upstream call"
    assert tenant_id != "MGV"


def test_no_entertainment_tool_input_model_accepts_tenant_id():
    """Structural proof, mirroring `mcp/tests/integration/test_surface_uniformity.py`'s
    and `agents/dining/tests/test_tenant_isolation.py`'s pattern for the one
    domain those files predate: every registered tool's input model must
    lack a `tenant_id` field."""
    from gateway.entertainment import server

    input_model_names = (
        "SearchEventsInput", "GetEventDetailInput", "CheckInventoryInput", "HoldSeatsInput",
        "ReleaseSeatHoldInput", "PurchaseTicketsInput", "GetOrderStatusInput", "RequestRefundInput",
    )
    for name in input_model_names:
        model = getattr(server, name)
        assert "tenant_id" not in model.model_fields, f"{name} must not accept tenant_id"
