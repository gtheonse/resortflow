"""Live-stack fixtures for `mcp/tests/integration/` (T044/T047/T048).

These tests run against real, running servers — the five sims and the gateway — not
mocks. Rather than requiring `make up`'s Docker stack to already be running (which a
plain `pytest` invocation cannot assume), this module boots each sim's real FastAPI app
and the real gateway app as `uvicorn.Server` instances in background threads, bound to
the exact hosts/ports `mcp/gateway/settings.py` and `docker-compose.yml` already agree
on (pms:9001, dining:9002, ticketing:9003, casino:9004, amenities:9005, gateway:9100).
Every request that follows is a real HTTP round-trip through real ASGI apps, including
`sims/_shared/faults.py`'s fault-injection middleware and the gateway's real MCP
streamable-HTTP wire transport (`graph/mcp_client.py`) — "live sims" in every sense that
matters for SC-008/SC-010/SC-015, just without a separate OS process per service.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

_SIM_SPECS = [
    ("pms", "sims.pms.main", 9001),
    ("dining", "sims.dining.main", 9002),
    ("ticketing", "sims.ticketing.main", 9003),
    ("casino", "sims.casino.main", 9004),
    ("amenities", "sims.amenities.main", 9005),
]
GATEWAY_PORT = 9100


class _ServerThread:
    """Runs one ASGI `app` under `uvicorn.Server` on its own event loop in a background
    thread, so lifespan (startup/shutdown) runs exactly as it would under a real
    `uvicorn` CLI invocation."""

    def __init__(self, app, *, host: str, port: int) -> None:
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.port = port
        self._error: BaseException | None = None

        def _run() -> None:
            try:
                self.server.run()
            except BaseException as exc:  # noqa: BLE001 — surfaced to the starting thread below
                self._error = exc

        self._thread = threading.Thread(target=_run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 30.0
        while not self.server.started:
            if self._error is not None:
                raise RuntimeError(f"server on port {self.port} failed to start") from self._error
            if not self._thread.is_alive():
                raise RuntimeError(f"server on port {self.port} thread exited before starting")
            if time.monotonic() > deadline:
                raise RuntimeError(f"server on port {self.port} did not start in time")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=10.0)


@pytest.fixture(scope="session")
def live_stack() -> Iterator[dict[str, int]]:
    """Boots all five sims + the gateway once per test session. Individual tests reset
    per-sim state via `POST /__sim__/reset` (the `reset_sims` fixture) rather than
    restarting servers, matching `sims/tests/fixtures/clients.py`'s own pattern."""
    import importlib

    servers: list[_ServerThread] = []
    try:
        for _name, module_path, port in _SIM_SPECS:
            module = importlib.import_module(module_path)
            server = _ServerThread(module.app, host="127.0.0.1", port=port)
            server.start()
            servers.append(server)

        from gateway.main import app as gateway_app

        gateway_server = _ServerThread(gateway_app, host="127.0.0.1", port=GATEWAY_PORT)
        gateway_server.start()
        servers.append(gateway_server)

        yield {name: port for (name, _mod, port) in _SIM_SPECS} | {"gateway": GATEWAY_PORT}
    finally:
        for server in reversed(servers):
            server.stop()


@pytest.fixture
def reset_sims(live_stack: dict[str, int]) -> None:
    """Resets every sim to its seeded baseline before a test runs — required since the
    sims persist state (SQLite) across the whole live session."""
    for name, _module_path, port in _SIM_SPECS:
        httpx.post(f"http://127.0.0.1:{port}/__sim__/reset", timeout=10.0)


@pytest.fixture(autouse=True)
def _fresh_upstream_clients_per_test(live_stack: dict[str, int]):
    """Autouse, session-wide fix for `UpstreamClient._recreate_client_for_tests`'s own
    documented problem: its five `UpstreamClient` singletons (one per domain, held by
    each `gateway.<domain>.server` module) are process-lifetime objects shared across
    every test file in this session, but pytest-asyncio's default function-scoped event
    loop means a fresh loop backs every single async test. `httpx.AsyncClient`'s
    connection pool binds to whichever loop is current the first time it's used, so the
    next test (or the next test *file*, entirely unrelated) that reuses the same
    singleton without a fresh client dies with `RuntimeError: Event loop is closed`
    trying to reuse a pool tied to an already-torn-down loop.

    Recreating every domain's client before each test — not just the one domain that
    particular test happens to exercise — means no test file has to know or care which
    other files ran before it in the same session; this fixture is the single, central
    place that keeps every real HTTP connection scoped to the loop that was alive when
    it opened."""
    import gateway.dining.server as _dining
    import gateway.entertainment.server as _entertainment
    import gateway.gaming.server as _gaming
    import gateway.stay.server as _stay
    import gateway.wellness.server as _wellness

    for module, attr in (
        (_dining, "dining_client"),
        (_entertainment, "entertainment_client"),
        (_stay, "stay_client"),
        (_wellness, "wellness_client"),
        (_gaming, "gaming_client"),
    ):
        getattr(module, attr)._recreate_client_for_tests()
    yield


@pytest.fixture
def mgv_dining_client(reset_sims, live_stack):
    """A real wire-transport `McpClient` scoped to dining, tenant MGV, with the full
    read/hold/commit dining scope set — the common case for T044/T047/T048's dining
    scenarios."""
    from graph.mcp_client import McpClient

    return McpClient(
        domain="dining",
        tenant_id="MGV",
        guest_id="test-guest-mgv",
        scopes=["dining:read", "dining:hold", "dining:commit"],
        base_url=f"http://127.0.0.1:{GATEWAY_PORT}",
    )


def make_dining_client(*, extra_headers: dict[str, str] | None = None):
    from graph.mcp_client import McpClient

    return McpClient(
        domain="dining",
        tenant_id="MGV",
        guest_id="test-guest-mgv",
        scopes=["dining:read", "dining:hold", "dining:commit"],
        base_url=f"http://127.0.0.1:{GATEWAY_PORT}",
        extra_headers=extra_headers,
    )
