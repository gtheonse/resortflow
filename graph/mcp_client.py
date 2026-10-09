"""T011 — shared real MCP wire-transport client (ADR-021).

Every domain's `Mcp<Domain>Tools` (dining's retrofit, plus entertainment/stay/wellness/
gaming, all new this feature) becomes a thin adapter delegating here rather than
reimplementing transport concerns five times — exactly what ADR-021 decided against
coexisting in-process/wire-transport patterns for.

This talks to the real, already-running gateway process (`mcp/gateway/main.py`) over the
official `mcp` SDK's streamable-HTTP transport — a real process boundary, real
serialization, real bearer-token auth header, replacing `agents/dining/tools.py`'s
previous in-process function call. `stateless_http=True` on the server side (see
`mcp/gateway/main.py`) means no server-side session affinity is required, so this client
opens one session per tool call rather than holding a long-lived connection — the
simplest correct thing, and consistent with this module's own scope boundary (see below).

Tenant/identity propagation: a bearer token is minted once per `McpClient` instance (one
per domain, per conversation — mirrors `McpDiningTools`'s previous one-`CallerIdentity`-
per-instance shape) via the gateway's own dev-token issuer
(`gateway._shared.identity.issue_token` — "not a network service, a function," per that
module's docstring; there is no separate identity provider in this project, so the agent
side reusing the same dev issuer the gateway already trusts is the only self-consistent
choice available). `tenant_id` still never comes from anywhere but the caller's own
verified identity, passed in at construction time — never derived from model output
(AGENTS.md hard rule), same as `McpDiningTools`.

What this module does not cover: retry/backoff/circuit-breaker policy (out of scope per
`entertainment-mcp-tool-contract.md`'s own "what this contract does not cover" — a
natural home here for a future feature, not built now).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import httpx2
from gateway._shared.identity import issue_token
from mcp.client.streamable_http import streamable_http_client

from mcp import ClientSession

GATEWAY_BASE_URL: str = os.environ.get("MCP_GATEWAY_URL", "http://localhost:9100")
"""Matches `mcp/gateway/main.py`'s uvicorn bind (ADR-011: one process, :9100)."""

_CALL_TIMEOUT_SECONDS: Final = 30.0


class McpToolError(Exception):
    """Raised on any gateway-reported failure — either a `NormalizedFailure`
    envelope returned as the tool's structured content, or the MCP
    transport itself reporting `isError`. Carries exactly the fields
    `graph.errors.classify()` needs, mirroring
    `agents.dining.tools.DiningToolError`'s shape so every domain's error
    path looks the same to a node."""

    def __init__(
        self,
        detail: str,
        *,
        error_class: str | None = None,
        vendor_code: str | None = None,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_class = error_class
        self.vendor_code = vendor_code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


def _is_normalized_failure(payload: Any) -> bool:
    """A `NormalizedFailure` (`gateway._shared.errors.NormalizedFailure`)
    round-trips over the wire as plain structured content — the same shape
    a success response has, just with a different, recognizable field set.
    Duck-typed on the two fields every `NormalizedFailure` always carries
    rather than importing the gateway's error model into this cross-cutting
    module (this module has no other gateway-internals dependency)."""
    return isinstance(payload, dict) and "error_class" in payload and "domain" in payload


class McpClient:
    """One real-transport client scoped to a single domain and a single
    guest's identity for the lifetime of a conversation — the wire-based
    successor to `McpDiningTools`'s in-process `CallerIdentity` context-var
    publication."""

    def __init__(
        self,
        *,
        domain: str,
        tenant_id: str,
        guest_id: str,
        scopes: list[str],
        base_url: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._domain = domain
        self._base_url = (base_url or GATEWAY_BASE_URL).rstrip("/")
        self._token = issue_token(sub=guest_id, tenant_id=tenant_id, scopes=scopes)
        self._extra_headers = extra_headers or {}
        """T042: e.g. `{"X-Sim-Fault": "inventory_race"}` — forwarded verbatim
        on every request; `gateway._shared.middleware.AuthenticationMiddleware`
        reads it back off and `_shared.upstream.UpstreamClient` relays it to
        the vendor sim (`graph/demo.py`'s `--inject-failure`/
        `--force-unavailable`). Empty for every real caller outside a demo run."""

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        url = f"{self._base_url}/{self._domain}/mcp"
        headers = {"Authorization": f"Bearer {self._token}", **self._extra_headers}
        async with (
            httpx2.AsyncClient(headers=headers, timeout=_CALL_TIMEOUT_SECONDS) as http_client,
            streamable_http_client(url, http_client=http_client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def call_tool(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        """Call `resortflow.<domain>.<tool_name>` over the wire. Returns the tool's
        `structuredContent` dict on success. Raises `McpToolError` for a
        `NormalizedFailure` envelope or a transport-level error — a node
        never has to distinguish the two.

        Every registered gateway tool function has the shape
        `async def tool(input: SomeInput, idempotency_key: str | None = None)`
        (`mcp/gateway/*/server.py`) — the MCP tool schema FastMCP derives
        from that signature therefore expects the call's `arguments` as
        `{"input": {...the tool's own fields...}, "idempotency_key": ...}`,
        never the caller's kwargs flattened at the top level. This method is
        the one place every domain's `Mcp<Domain>Tools` funnels through, so
        it is the one place this wrapping needs to happen."""
        full_name = f"resortflow.{self._domain}.{tool_name}"
        idempotency_key = kwargs.pop("idempotency_key", None)
        arguments: dict[str, Any] = {"input": kwargs}
        if idempotency_key is not None:
            arguments["idempotency_key"] = idempotency_key
        async with self._session() as session:
            result = await session.call_tool(full_name, arguments=arguments)
        payload = result.structured_content
        if result.is_error or _is_normalized_failure(payload):
            payload = payload or {}
            raise McpToolError(
                str(payload.get("detail", "tool call failed")),
                error_class=payload.get("error_class"),
                vendor_code=payload.get("vendor_code"),
                http_status=payload.get("http_status"),
                retry_after_seconds=payload.get("retry_after_seconds"),
            )
        return payload or {}
