"""T048 — indeterminate-commit test (FR-032, SC-015).

A `commit`-scoped call whose retries exhaust while the breaker is OPEN classifies
`REQUIRES_HUMAN`, never `RETRYABLE`, and carries `effect_status="unresolved"`. This is
the exact signal `graph/ledger.py`'s write-ahead hold ledger exists to make
recoverable: a `REQUIRES_HUMAN` + `effect_status="unresolved"` outcome means "we cannot
rule out the mutation having landed vendor-side," which is precisely the case a future
reconciler (reading `hold_ledger` rows stuck at `PENDING`/`HELD`) has to resolve by
checking the vendor directly rather than assuming success or failure either way.

Per `_shared/upstream.py`'s actual `UpstreamClient.call`, an OPEN breaker refuses the
call via `before_call()` *before* the retry loop's `except RetryableUpstreamError`
branch is ever reached — `BreakerOpen` is not a `RetryableUpstreamError`, so
`tenacity`'s `retry_if_exception_type` does not retry it at all. This test proves the
call classification this produces (still `REQUIRES_HUMAN`/`unresolved` for a mutating
call, per `UpstreamClient.call`'s own `except resilience.BreakerOpen` branch) rather
than asserting a literal multi-attempt retry loop that the breaker-open path
deliberately short-circuits.

Called in-process (`dining_server.confirm_reservation` directly, with a `CallerIdentity`
published on the same context var the real middleware would use) rather than over the
MCP wire transport: the MCP SDK's typed-return validation chokes when a tool declared to
return `Reservation` actually returns a `NormalizedFailure` (an unrelated pre-existing
gap surfaced while writing `test_idempotent_retry.py`), so the wire path cannot recover
`effect_status` cleanly. Calling the registered tool wrapper directly still exercises
the real mechanism this task cares about — `register_tool`'s wrapper, `UpstreamClient`,
`CircuitBreaker`, and `build_normalized_failure` — none of which are wire-transport
concerns.
"""

from __future__ import annotations

import uuid

import pytest
from gateway._shared.errors import NormalizedFailure
from gateway._shared.identity import CallerIdentity
from gateway._shared.middleware import _current_identity
from gateway._shared.resilience import BREAKER_FAILURE_THRESHOLD
from gateway.dining import server as dining_server
from gateway.dining.server import ConfirmReservationInput

from .conftest import make_dining_client

pytestmark = pytest.mark.asyncio


def _set_full_dining_identity() -> object:
    identity = CallerIdentity(
        sub="indeterminate-commit-test",
        tenant_id="MGV",
        scopes=("dining:read", "dining:hold", "dining:commit"),
        iat=0,
        exp=99_999_999_999,
    )
    return _current_identity.set(identity)


@pytest.fixture
def force_breaker_closed_after():
    """However a test leaves `dining_client.breaker`, restore it to a fresh CLOSED
    state afterward so later tests (and other files sharing this process's singleton
    breaker) never inherit residual OPEN/HALF_OPEN state."""
    yield
    breaker = dining_server.dining_client.breaker
    breaker._state = "CLOSED"
    breaker._consecutive_failures = 0
    breaker._opened_at = None
    breaker._half_open_probe_in_flight = False


async def test_commit_call_against_open_breaker_is_requires_human_never_retryable(
    reset_sims, force_breaker_closed_after
):
    # A real hold, placed normally (breaker CLOSED), so the confirm call below is
    # otherwise entirely legitimate -- the only thing standing between it and success
    # is the breaker.
    hold_client = make_dining_client()
    hold_result = await hold_client.call_tool(
        "hold_table",
        venue_id="ven-mgv-01",
        date="2026-12-31",
        time="19:00",
        party_size=2,
        idempotency_key=f"resortflow:test-indeterminate:{uuid.uuid4().hex}:1",
    )
    hold_id = hold_result["hold_id"]

    breaker = dining_server.dining_client.breaker
    assert breaker.state == "CLOSED"
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    assert breaker.state == "OPEN"

    token = _set_full_dining_identity()
    try:
        result = await dining_server.confirm_reservation(
            input=ConfirmReservationInput(hold_id=hold_id, guest={"name": "Test Guest"}),
            idempotency_key=f"resortflow:test-indeterminate:{uuid.uuid4().hex}:1",
        )
    finally:
        _current_identity.reset(token)

    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "REQUIRES_HUMAN"
    assert result.error_class != "RETRYABLE"
    assert result.effect_status == "unresolved"
    assert result.domain == "dining"


async def test_read_call_against_open_breaker_is_retryable_not_requires_human(
    reset_sims, force_breaker_closed_after
):
    """Contrast case: the same OPEN breaker, for a non-mutating call, classifies
    RETRYABLE (never REQUIRES_HUMAN/unresolved) -- `effect_status="unresolved"` is
    reserved for calls that could plausibly have mutated vendor state (FR-032); a
    read never could have."""
    breaker = dining_server.dining_client.breaker
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    assert breaker.state == "OPEN"

    token = _set_full_dining_identity()
    try:
        from gateway.dining.server import CheckAvailabilityInput

        result = await dining_server.check_availability(
            input=CheckAvailabilityInput(venue_id="ven-mgv-01", date="2026-12-31", party_size=2)
        )
    finally:
        _current_identity.reset(token)

    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "RETRYABLE"
    assert result.effect_status is None


async def test_open_breaker_never_produces_a_retryable_classification_for_a_commit_call(
    reset_sims, force_breaker_closed_after
):
    """Repeats the primary assertion across several fresh idempotency keys, ruling out
    a fluke tied to one particular key or hold."""
    hold_client = make_dining_client()
    breaker = dining_server.dining_client.breaker

    for _ in range(3):
        hold_result = await hold_client.call_tool(
            "hold_table",
            venue_id="ven-mgv-01",
            date="2026-12-31",
            time="20:00",
            party_size=2,
            idempotency_key=f"resortflow:test-indeterminate-multi:{uuid.uuid4().hex}:1",
        )
        hold_id = hold_result["hold_id"]

        for _ in range(BREAKER_FAILURE_THRESHOLD):
            breaker.record_failure()
        assert breaker.state == "OPEN"

        token = _set_full_dining_identity()
        try:
            result = await dining_server.confirm_reservation(
                input=ConfirmReservationInput(hold_id=hold_id, guest={"name": "Test Guest"}),
                idempotency_key=f"resortflow:test-indeterminate-multi:{uuid.uuid4().hex}:1",
            )
        finally:
            _current_identity.reset(token)

        assert result.error_class == "REQUIRES_HUMAN"
        assert result.effect_status == "unresolved"

        # Reset for the next iteration.
        breaker._state = "CLOSED"
        breaker._consecutive_failures = 0
        breaker._opened_at = None
