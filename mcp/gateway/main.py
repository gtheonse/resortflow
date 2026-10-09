"""Gateway entrypoint: build five independent FastMCP (`MCPServer`) instances, mount each
one's real streamable-HTTP ASGI app at /dining/mcp, /entertainment/mcp, /stay/mcp,
/wellness/mcp, /gaming/mcp behind the shared auth + tracing middleware chain, served as
one uvicorn app on :9100 (ADR-011).

Feature 005 (ADR-021) is what makes this a real wire hop for the first time:
`MCPServer.streamable_http_app()` returns the actual MCP-protocol ASGI app (not the bare
`MCPServer` instance, which implements no ASGI interface itself) — `graph/mcp_client.py`
is the client side of this same real transport. `stateless_http=True`: this gateway has no
resumable-session/event-store infrastructure, and every call already carries a fresh
bearer token per `McpClient` instance, so there is no session state worth keeping across
requests — each HTTP request is independently authenticated and independently routed to
its tool, exactly like the in-process calls this replaces.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from gateway._shared.middleware import AuthenticationMiddleware
from gateway._shared.tracing import initialize_tracer
from gateway.dining.server import app as dining_app
from gateway.entertainment.server import app as entertainment_app
from gateway.gaming.server import app as gaming_app
from gateway.stay.server import app as stay_app
from gateway.wellness.server import app as wellness_app

# --- Build each domain's real streamable-HTTP ASGI app up front (ADR-021) ----------
# Each is a *separate* Starlette app with its own `session_manager.run()` lifespan
# (`MCPServer.streamable_http_app()`'s own `router.lifespan_context`) — mounting via
# `app.mount()` alone does NOT invoke that lifespan (FastAPI/Starlette does not
# automatically forward lifespan events into a mounted sub-app); calling a tool without
# starting it raises "Task group is not initialized. Make sure to use run()." at request
# time. The parent's own `lifespan` below is what actually starts all five.
_TRANSPORT_SECURITY = TransportSecuritySettings(
    # DNS-rebinding protection (on by default) rejects every Host header
    # unless explicitly allowlisted here — this is the gateway's real bind
    # target (ADR-011: uvicorn on :9100), "mcp-gateway:9100" (the
    # docker-compose service DNS name every containerized caller — the
    # orchestrator, hold-ledger-reaper — actually uses; found missing when
    # Phase 7.5's gap-closure work first ran a real container-to-container
    # call and got a 421 Misdirected Request), plus "testserver" for
    # Starlette's own TestClient default Host header, used by this
    # project's integration tests (`mcp/tests/integration/`).
    allowed_hosts=["localhost:9100", "127.0.0.1:9100", "mcp-gateway:9100", "testserver"],
)

DINING_ASGI = dining_app.streamable_http_app(stateless_http=True, transport_security=_TRANSPORT_SECURITY)
ENTERTAINMENT_ASGI = entertainment_app.streamable_http_app(stateless_http=True, transport_security=_TRANSPORT_SECURITY)
STAY_ASGI = stay_app.streamable_http_app(stateless_http=True, transport_security=_TRANSPORT_SECURITY)
WELLNESS_ASGI = wellness_app.streamable_http_app(stateless_http=True, transport_security=_TRANSPORT_SECURITY)
GAMING_ASGI = gaming_app.streamable_http_app(stateless_http=True, transport_security=_TRANSPORT_SECURITY)

_DOMAIN_ASGI_APPS = (DINING_ASGI, ENTERTAINMENT_ASGI, STAY_ASGI, WELLNESS_ASGI, GAMING_ASGI)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the tracer, then enter every mounted domain app's own
    lifespan (starting each `MCPServer`'s streamable-HTTP session manager)
    for the life of this process."""
    initialize_tracer(use_console_exporter=False)
    async with AsyncExitStack() as stack:
        for domain_app in _DOMAIN_ASGI_APPS:
            await stack.enter_async_context(domain_app.router.lifespan_context(domain_app))
        yield


app = FastAPI(title="ResortFlow MCP Gateway", lifespan=lifespan)

# Add the authentication middleware — verifies bearer token once, ahead of all
# sub-apps, and publishes CallerIdentity on a context variable.
app.add_middleware(AuthenticationMiddleware)


# --- Mount domain servers (ADR-011: one process, five FastMCP instances; ADR-021: each
# mounted as its real streamable-HTTP ASGI app, not the bare MCPServer object) ---------
app.mount("/dining", DINING_ASGI)
app.mount("/entertainment", ENTERTAINMENT_ASGI)
app.mount("/stay", STAY_ASGI)
app.mount("/wellness", WELLNESS_ASGI)
app.mount("/gaming", GAMING_ASGI)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9100)
