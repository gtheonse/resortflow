"""T047 — resilience integration suite against live sims (SC-010).

One vendor forced failing (dining, via `X-Sim-Fault: error_500`) opens only its own
breaker; the other four domains' breakers stay CLOSED and their calls keep succeeding;
recovery is automatic after the cooldown elapses, with no operator action.

Drives real MCP wire-transport calls (`graph.mcp_client.McpClient`) through the real
gateway against the real, live sims (`mcp/tests/integration/conftest.py`'s
`live_stack`) for the "other four keep succeeding" half, and reaches into the
in-process `CircuitBreaker` instances the gateway's `UpstreamClient`s already hold
(`gateway.<domain>.server.<domain>_client.breaker`) to observe state transitions
directly and to shrink the 30s cooldown for a test that doesn't want to sleep half a
minute.
"""

from __future__ import annotations

import asyncio

import pytest
from gateway.dining import server as dining_server
from gateway.entertainment import server as entertainment_server
from gateway.gaming import server as gaming_server
from gateway.stay import server as stay_server
from gateway.wellness import server as wellness_server

from graph.mcp_client import McpClient

pytestmark = pytest.mark.asyncio

GATEWAY_PORT = 9100

DOMAIN_CLIENTS = {
    "dining": dining_server.dining_client,
    "entertainment": entertainment_server.entertainment_client,
    "stay": stay_server.stay_client,
    "wellness": wellness_server.wellness_client,
    "gaming": gaming_server.gaming_client,
}


def _full_scope_client(domain: str, *, extra_headers: dict[str, str] | None = None) -> McpClient:
    return McpClient(
        domain=domain,
        tenant_id="MGV",
        guest_id=f"resilience-test-{domain}",
        scopes=[f"{domain}:read", f"{domain}:hold", f"{domain}:commit"],
        base_url=f"http://127.0.0.1:{GATEWAY_PORT}",
        extra_headers=extra_headers,
    )


async def _other_domain_read_calls_succeed() -> None:
    """One cheap, real, live read call per non-dining domain -- proves each is
    unaffected by dining's breaker state."""
    entertainment = _full_scope_client("entertainment")
    await entertainment.call_tool("search_events", classification=None, date=None)

    stay = _full_scope_client("stay")
    await stay.call_tool("search_rooms", arrival="2026-12-30", departure="2026-12-31", adults=2)

    wellness = _full_scope_client("wellness")
    await wellness.call_tool("list_services", category=None)

    gaming = _full_scope_client("gaming")
    await gaming.call_tool("list_casino_floor", game_type=None)


@pytest.fixture(autouse=True)
def _restore_all_breakers_after():
    """Every breaker touched by this file (dining is deliberately forced OPEN; the
    other four are only ever read, never mutated, but this keeps the invariant
    explicit and cheap) is reset to a fresh CLOSED state afterward -- these are
    process-lifetime singletons shared with every other test file in this session."""
    yield
    for client in DOMAIN_CLIENTS.values():
        breaker = client.breaker
        breaker._state = "CLOSED"
        breaker._consecutive_failures = 0
        breaker._opened_at = None
        breaker._half_open_probe_in_flight = False
        from gateway._shared.resilience import BREAKER_COOLDOWN_SECONDS

        breaker.cooldown_seconds = BREAKER_COOLDOWN_SECONDS


async def test_forcing_one_vendor_failing_opens_only_its_own_breaker(reset_sims):
    for client in DOMAIN_CLIENTS.values():
        assert client.breaker.state == "CLOSED"

    failing_dining = _full_scope_client("dining", extra_headers={"X-Sim-Fault": "error_500"})

    # error_500 is a TERMINAL-shaped-by-status-code 5xx -> RETRYABLE, and every failed
    # attempt (RETRY_ATTEMPTS per call) counts toward the breaker's consecutive-failure
    # streak. A handful of calls is enough to cross BREAKER_FAILURE_THRESHOLD (5).
    for _ in range(3):
        try:
            await failing_dining.call_tool(
                "check_availability", venue_id="ven-mgv-01", date="2026-12-31", party_size=2
            )
        except Exception:  # noqa: BLE001, S110 -- any failure shape is expected; only breaker state matters
            pass

    assert dining_server.dining_client.breaker.state == "OPEN"

    # The other four domains' breakers never moved.
    for name, client in DOMAIN_CLIENTS.items():
        if name == "dining":
            continue
        assert client.breaker.state == "CLOSED", f"{name}'s breaker must be unaffected by dining's failures"

    # And they keep succeeding for real, live calls -- dining's breaker being OPEN
    # does not degrade any other domain's gateway->vendor path.
    await _other_domain_read_calls_succeed()


async def test_recovery_is_automatic_after_cooldown_with_no_operator_action(reset_sims):
    breaker = dining_server.dining_client.breaker
    # Shrink the cooldown so this test doesn't have to sleep 30 real seconds --
    # BREAKER_COOLDOWN_SECONDS itself is a module constant `CircuitBreaker` reads only
    # through its own `cooldown_seconds` dataclass field, which is freely overridable
    # per instance.
    breaker.cooldown_seconds = 0.3

    from gateway._shared.resilience import BREAKER_FAILURE_THRESHOLD

    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    assert breaker.state == "OPEN"

    # Still within the (shrunk) cooldown: refused.
    with pytest.raises(Exception):  # noqa: B017 -- shape of the OPEN-breaker refusal isn't asserted here
        client = _full_scope_client("dining")
        await client.call_tool(
            "check_availability", venue_id="ven-mgv-01", date="2026-12-31", party_size=2
        )

    await asyncio.sleep(0.35)
    assert breaker.state == "HALF_OPEN"

    # No operator action -- the very next real call is the automatic HALF_OPEN probe,
    # against the real live dining sim, and it succeeds (the sim was never actually
    # unhealthy, only fault-injected earlier and not now).
    client = _full_scope_client("dining")
    result = await client.call_tool(
        "check_availability", venue_id="ven-mgv-01", date="2026-12-31", party_size=2
    )
    assert "slots" in result

    assert breaker.state == "CLOSED"
    assert breaker.consecutive_failures == 0


async def test_recovered_breaker_serves_subsequent_calls_normally(reset_sims):
    """Belt-and-braces: after the automatic HALF_OPEN->CLOSED transition, dining
    keeps working for further calls -- recovery is not a one-shot fluke."""
    breaker = dining_server.dining_client.breaker
    breaker.cooldown_seconds = 0.2

    from gateway._shared.resilience import BREAKER_FAILURE_THRESHOLD

    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    await asyncio.sleep(0.25)
    assert breaker.state == "HALF_OPEN"

    client = _full_scope_client("dining")
    await client.call_tool("check_availability", venue_id="ven-mgv-01", date="2026-12-31", party_size=2)
    assert breaker.state == "CLOSED"

    for _ in range(3):
        result = await client.call_tool(
            "check_availability", venue_id="ven-mgv-01", date="2026-12-31", party_size=2
        )
        assert "slots" in result
    assert breaker.state == "CLOSED"
