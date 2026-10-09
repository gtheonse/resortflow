"""Shared real-wire-transport test fixtures (T026/T027/T036/T037/T038).

Mirrors `agents/tests/test_tenant_isolation.py`'s pattern exactly: a fresh
`streamable_http_app()` + minimal FastAPI wrapper (auth middleware ahead of one
mounted domain app) built per call, driven over `httpx2.ASGITransport` — a real
MCP JSON-RPC session over a real streamable-HTTP transport, in-process (no live
socket, no live vendor sim), which is what makes this the CI-gating proof
rather than a python-object-call shortcut.

Centralized here so T026, T027, T036, T037, T038's tests build the app the
same way instead of five near-duplicates drifting apart.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import gateway.dining.server as dining_server
import gateway.entertainment.server as entertainment_server
import gateway.gaming.server as gaming_server
import gateway.stay.server as stay_server
import gateway.wellness.server as wellness_server
import httpx2
from fastapi import FastAPI
from gateway._shared.identity import issue_token
from gateway._shared.middleware import AuthenticationMiddleware
from gateway._shared.tracing import initialize_tracer
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings

from mcp import ClientSession

_TRANSPORT_SECURITY = TransportSecuritySettings(allowed_hosts=["testserver"])


@dataclass(frozen=True)
class DomainInfo:
    """Everything a generic cross-domain test needs to know about one domain,
    without hardcoding per-domain tool knowledge into every test file."""

    name: str
    module: Any
    client_attr: str
    """Attribute name on `module` of the `UpstreamClient` instance a tool body
    calls — the one seam these tests mock instead of running a live vendor sim."""
    read_tool: str
    """A `resortflow.<domain>.<verb>_<noun>` read tool usable with a single resource-id
    argument, for the isolation/uniformity suites."""
    read_tool_arg_name: str
    """The field on that tool's input model carrying the resource/guest id."""
    read_response_factory: Callable[[], Any]
    """Builds a minimal valid instance of that tool's declared response model."""
    all_scopes: tuple[str, ...] = field(default_factory=tuple)
    read_tool_args: Callable[[str], dict[str, Any]] = field(default=None)  # type: ignore[assignment]
    """Given a resource id, returns a COMPLETE valid input dict for `read_tool`
    (some domains' read tools require more than the id — e.g. dining's
    `check_availability` also requires `date`/`party_size`, wellness's
    `check_service_availability` also requires `date`). Defaults to
    `{read_tool_arg_name: resource_id}` when the tool takes only the id."""

    def __post_init__(self) -> None:
        if self.read_tool_args is None:
            object.__setattr__(
                self, "read_tool_args", lambda rid: {self.read_tool_arg_name: rid}
            )


DOMAINS: dict[str, DomainInfo] = {
    "dining": DomainInfo(
        name="dining",
        module=dining_server,
        client_attr="dining_client",
        read_tool="check_availability",
        read_tool_arg_name="venue_id",
        read_tool_args=lambda rid: {"venue_id": rid, "date": "2026-10-01", "party_size": 2},
        read_response_factory=lambda: dining_server.AvailabilityResponse(
            venue_id="v1", date="2026-10-01", slots=[]
        ),
        all_scopes=("dining:read", "dining:hold", "dining:commit"),
    ),
    "entertainment": DomainInfo(
        name="entertainment",
        module=entertainment_server,
        client_attr="entertainment_client",
        read_tool="get_event_detail",
        read_tool_arg_name="event_id",
        read_response_factory=lambda: entertainment_server.EventDetail.model_construct(
            **_first_valid(entertainment_server.EventDetail)
        ),
        all_scopes=("entertainment:read", "entertainment:hold", "entertainment:commit"),
    ),
    "stay": DomainInfo(
        name="stay",
        module=stay_server,
        client_attr="stay_client",
        read_tool="get_stay_details",
        read_tool_arg_name="stay_id",
        read_response_factory=lambda: stay_server.Stay.model_construct(
            **_first_valid(stay_server.Stay)
        ),
        all_scopes=("stay:read", "stay:hold", "stay:commit"),
    ),
    "wellness": DomainInfo(
        name="wellness",
        module=wellness_server,
        client_attr="wellness_client",
        read_tool="check_service_availability",
        read_tool_arg_name="service_id",
        read_tool_args=lambda rid: {"service_id": rid, "date": "2026-10-01"},
        read_response_factory=lambda: wellness_server.AvailabilityResponse.model_construct(
            **_first_valid(wellness_server.AvailabilityResponse)
        ),
        all_scopes=("wellness:read", "wellness:hold", "wellness:commit"),
    ),
    "gaming": DomainInfo(
        name="gaming",
        module=gaming_server,
        client_attr="gaming_client",
        read_tool="check_rg_status",
        read_tool_arg_name="guest_id",
        read_response_factory=lambda: gaming_server.ResponsibleGamingStatus.model_construct(
            **_first_valid(gaming_server.ResponsibleGamingStatus)
        ),
        all_scopes=("gaming:read",),
    ),
}


def _first_valid(model_cls: type) -> dict[str, Any]:
    """Best-effort minimal field set for `model_construct` (bypasses validation,
    fine for a test double we control end-to-end) — every field defaults to a
    small type-appropriate placeholder so schema shape is irrelevant to callers
    that only care about routing/tenancy, not payload content."""
    values: dict[str, Any] = {}
    for field_name, field_info in model_cls.model_fields.items():
        ann = field_info.annotation
        if ann is str or ann == (str | None):
            values[field_name] = f"stub-{field_name}"
        elif ann is int:
            values[field_name] = 0
        elif ann is bool:
            values[field_name] = False
        elif ann is float:
            values[field_name] = 0.0
        elif ann is dict:
            # A required `dict` field (e.g. entertainment's `Event.venue`/
            # `price_range`) has no `None`-shaped valid value once the output
            # schema requires `{"type": "object"}` -- `{}` satisfies both the
            # schema and `extra`-forbidding nested validation.
            values[field_name] = {}
        else:
            values[field_name] = [] if "list" in str(ann).lower() else None
    return values


def build_domain_app(info: DomainInfo) -> FastAPI:
    """A minimal standalone one-domain gateway, mirroring `gateway.main`'s real
    shape (auth middleware ahead of the mounted domain app) but built fresh per
    test so each gets its own `StreamableHTTPSessionManager` lifespan."""
    domain_asgi = info.module.app.streamable_http_app(
        stateless_http=True, transport_security=_TRANSPORT_SECURITY
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize_tracer(use_console_exporter=False)
        async with domain_asgi.router.lifespan_context(domain_asgi):
            yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(AuthenticationMiddleware)
    app.mount(f"/{info.name}", domain_asgi)
    return app


@asynccontextmanager
async def domain_session(info: DomainInfo, *, token: str | None, extra_headers: dict | None = None):
    """Open a real MCP `ClientSession` against a fresh in-process app for one
    domain. `token=None` sends no `Authorization` header at all (the "absent
    credential" refusal case); pass a malformed/expired/tampered string
    directly to exercise the other four refusal conditions."""
    app = build_domain_app(info)
    transport = httpx2.ASGITransport(app=app)
    headers = {"Host": "testserver", **(extra_headers or {})}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with app.router.lifespan_context(app), httpx2.AsyncClient(
        transport=transport, headers=headers, base_url="http://testserver"
    ) as http_client, streamable_http_client(
        f"http://testserver/{info.name}/mcp", http_client=http_client
    ) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def mint(tenant_id: str, scopes: list[str] | tuple[str, ...], *, sub: str = "test-harness") -> str:
    return issue_token(sub=sub, tenant_id=tenant_id, scopes=list(scopes))
