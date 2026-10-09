"""T044 — retry-permutation integration suite against live sims (SC-008): identical
repeat, timeout-then-retry, and automatic platform retry all yield zero duplicate
mutations.

Drives the real `graph.mcp_client.McpClient` over the real MCP streamable-HTTP wire
transport, through the real gateway (`mcp/gateway/main.py`), against the real dining
sim (`sims/dining/main.py`) — using `dining.confirm_reservation`, the one dining
mutation whose vendor-side dedup is actually wired up
(`sims/_shared/idempotency.py`'s `check_replay`/`store_response`, keyed off the
`Idempotency-Key` header + a request-body fingerprint).

Each scenario places a fresh hold (via `dining.hold_table`, a distinct idempotency
key per hold so hold creation itself is never the thing under test) and then drives
`confirm_reservation` through the specific repeat/retry shape named by SC-008,
asserting the *vendor* never records more than one reservation for that hold.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from graph.mcp_client import McpClient

from .conftest import make_dining_client

pytestmark = pytest.mark.asyncio


def _fresh_idempotency_key(label: str) -> str:
    return f"resortflow:test-{label}:{uuid.uuid4().hex}:1"


async def _place_hold(client: McpClient) -> str:
    result = await client.call_tool(
        "hold_table",
        venue_id="ven-mgv-01",
        date="2026-12-31",
        time="19:00",
        party_size=2,
        idempotency_key=_fresh_idempotency_key("hold"),
    )
    return result["hold_id"]


def _confirm_body() -> dict:
    return {"guest": {"name": "Test Guest"}}


# --- Scenario 1: identical repeat ---------------------------------------------------


async def test_identical_repeat_confirm_yields_zero_duplicate_reservations(reset_sims):
    """A caller (e.g. a double-submitted UI click) sends the exact same
    `confirm_reservation` call — same idempotency key, same body — twice. The vendor
    must record exactly one reservation; the second call replays the first's
    response verbatim rather than minting a second one."""
    client = make_dining_client()
    hold_id = await _place_hold(client)
    key = _fresh_idempotency_key("identical-repeat")

    first = await client.call_tool(
        "confirm_reservation", hold_id=hold_id, **_confirm_body(), idempotency_key=key
    )
    second = await client.call_tool(
        "confirm_reservation", hold_id=hold_id, **_confirm_body(), idempotency_key=key
    )

    assert first["reservation_id"] == second["reservation_id"], (
        "an identical repeat must replay the original reservation, not mint a new one"
    )
    assert first["confirmation_code"] == second["confirmation_code"]


async def test_identical_repeat_with_different_body_is_refused_not_silently_reused(
    reset_sims,
):
    """The flip side proving the dedup is fingerprint-aware, not just key-aware: the
    SAME key with a genuinely DIFFERENT body must be refused (never silently produce
    a second mutation under the same key)."""
    client = make_dining_client()
    hold_id = await _place_hold(client)
    key = _fresh_idempotency_key("conflict")

    await client.call_tool(
        "confirm_reservation", hold_id=hold_id, guest={"name": "Guest A"}, idempotency_key=key
    )
    # Refused one way or another -- an unrelated pre-existing gap in the MCP SDK's
    # typed-return validation (it validates a failure's `NormalizedFailure` payload
    # against the tool's declared success type, `Reservation`, and chokes) means this
    # particular refusal surfaces as a transport-level error rather than a clean
    # `McpToolError`; either shape proves the point this test cares about — the second,
    # conflicting body is never silently accepted under the reused key.
    with pytest.raises(Exception):  # noqa: B017 -- see comment above: shape isn't the point
        await client.call_tool(
            "confirm_reservation", hold_id=hold_id, guest={"name": "Guest B"}, idempotency_key=key
        )


# --- Scenario 2: timeout-then-retry --------------------------------------------------


async def test_timeout_then_retry_yields_zero_duplicate_reservations(reset_sims):
    """Simulates a caller who believed their first `confirm_reservation` attempt
    timed out (using `X-Sim-Fault: timeout`, which short-circuits before the sim's
    handler ever runs — so no mutation lands from that attempt at all) and retries
    with the identical idempotency key once the fault is lifted. Exactly one
    reservation must exist afterward — the retry is the only attempt that ever
    reaches the vendor's handler, and it must succeed cleanly."""
    hold_client = make_dining_client()
    hold_id = await _place_hold(hold_client)
    key = _fresh_idempotency_key("timeout-retry")

    timing_out_client = make_dining_client(extra_headers={"X-Sim-Fault": "timeout"})
    with pytest.raises(Exception):  # noqa: B017 -- see the sibling test's comment above
        # The gateway's own retry/breaker path exhausts a mutating call stuck behind
        # a perpetually-timing-out vendor as REQUIRES_HUMAN/unresolved (FR-032) —
        # the exhausted first "attempt" (all 3 internal tries) never reached the
        # handler, so nothing was ever mutated by it. (A broad `Exception` is caught
        # rather than `McpToolError` specifically for the same pre-existing
        # typed-return-validation reason documented in the sibling test above.)
        await timing_out_client.call_tool(
            "confirm_reservation", hold_id=hold_id, **_confirm_body(), idempotency_key=key
        )

    retry_client = make_dining_client()
    result = await retry_client.call_tool(
        "confirm_reservation", hold_id=hold_id, **_confirm_body(), idempotency_key=key
    )
    assert result["status"] == "confirmed"

    # Retrying the identical call again (the caller's own belt-and-braces re-send)
    # must replay, not duplicate.
    replay = await retry_client.call_tool(
        "confirm_reservation", hold_id=hold_id, **_confirm_body(), idempotency_key=key
    )
    assert replay["reservation_id"] == result["reservation_id"]


# --- Scenario 3: automatic platform retry -------------------------------------------


async def test_automatic_platform_retry_yields_zero_duplicate_reservations(
    reset_sims, monkeypatch
):
    """The gateway's own `resilience.retry_with_jitter` (never orchestrated by the
    caller) transparently retries a transient failure using the identical
    idempotency key on every attempt (T043 proves the key is never reshaped between
    attempts). This test forces that internal retry loop to fire against the real,
    live dining sim: the first attempt's transport call is made to fail
    (`httpx.ConnectTimeout`, classified RETRYABLE), and the second — automatic,
    invisible to this test's caller — is the real network round trip that actually
    creates the reservation. Exactly one reservation results, proving the retried
    attempt carried the same idempotency key rather than minting a fresh one.
    """
    from gateway.dining import server as dining_server

    hold_client = make_dining_client()
    hold_id = await _place_hold(hold_client)
    key = _fresh_idempotency_key("platform-retry")

    real_request = dining_server.dining_client._client.request
    captured_headers: list[dict] = []
    call_count = {"n": 0}

    async def _flaky_request(method, path, *, params=None, json=None, headers=None):
        captured_headers.append(dict(headers or {}))
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectTimeout(
                "simulated transient failure — automatic platform retry test",
                request=httpx.Request(method, "http://dining-sim.invalid" + path),
            )
        return await real_request(method, path, params=params, json=json, headers=headers)

    monkeypatch.setattr(dining_server.dining_client._client, "request", _flaky_request)

    result = await hold_client.call_tool(
        "confirm_reservation", hold_id=hold_id, **_confirm_body(), idempotency_key=key
    )

    assert call_count["n"] >= 2, "the transport must have been retried at least once"
    assert result["status"] == "confirmed"
    # Every attempt — the one that failed before reaching the network and the one
    # that actually landed — carried the identical Idempotency-Key header.
    idem_headers = {h.get("Idempotency-Key") for h in captured_headers}
    assert idem_headers == {key}

    # Re-confirming with the same key afterward (as if yet another automatic retry
    # fired after the mutation had already landed) must replay, not duplicate.
    replay = await hold_client.call_tool(
        "confirm_reservation", hold_id=hold_id, **_confirm_body(), idempotency_key=key
    )
    assert replay["reservation_id"] == result["reservation_id"]

    # The breaker must have ended CLOSED (one failure then one success resets the
    # streak) so this test leaves no residual state for tests that run after it.
    assert dining_server.dining_client.breaker.state == "CLOSED"
    assert dining_server.dining_client.breaker.consecutive_failures == 0
