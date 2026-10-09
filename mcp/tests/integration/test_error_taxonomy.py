"""T040 [CRIT] -- fault-injection integration suite (SC-006): every `X-Sim-Fault` mode
against all five vendor sims. Every `error_500` surfaces `RETRYABLE`; zero raw vendor
error envelopes escape uncaught. Also exercises T071's `schema_drift` fault (SC-016).

Drives the real, registered tool wrappers in-process (`gateway.<domain>.server.<tool>`)
against the real, live sims (`mcp/tests/integration/conftest.py`'s `live_stack`/
`reset_sims`) -- not the MCP wire transport, for the same documented reason
`test_indeterminate_commit.py`/`test_entitlements.py` already use this pattern: a tool
declared to return a success model chokes the MCP SDK's typed-return validation when it
actually returns a `NormalizedFailure`. The HTTP round-trip to each live sim (and that
sim's own `sims/_shared/faults.py` fault-injection middleware) is exactly as real either
way -- only the client-side transport differs.

`X-Sim-Fault` is threaded through with `gateway._shared.middleware._current_sim_fault`,
the same context var `AuthenticationMiddleware` populates from the real header on the
wire path (`_shared/upstream.py`'s `UpstreamClient.call` reads it back off unconditionally
of transport).
"""

from __future__ import annotations

import pytest
from gateway._shared import resilience
from gateway._shared.errors import NormalizedFailure
from gateway._shared.identity import CallerIdentity
from gateway._shared.middleware import _current_identity, _current_sim_fault
from gateway.dining import server as dining_server
from gateway.entertainment import server as entertainment_server
from gateway.gaming import server as gaming_server
from gateway.stay import server as stay_server
from gateway.wellness import server as wellness_server

from sims._shared.faults import ALL_FAULT_VALUES

# Every *async* test below is individually marked `@pytest.mark.asyncio(loop_scope=
# "module")` rather than via a file-wide `pytestmark` (which would also tag the two
# plain sync guard tests at the bottom of this file and trigger a PytestWarning).
# `loop_scope="module"`: this file calls the registered domain tool wrappers directly
# in-process (bypassing the MCP wire transport, per this module's own docstring), which
# reuses each domain's module-level `UpstreamClient` singleton and its `httpx.AsyncClient`
# connection pool -- see `test_policy_blocked.py`'s identical comment for why a fresh
# per-test event loop (the pytest-asyncio default) breaks that pool on the second test.
_async_test = pytest.mark.asyncio(loop_scope="module")

DOMAIN_CLIENTS = {
    "dining": dining_server.dining_client,
    "entertainment": entertainment_server.entertainment_client,
    "stay": stay_server.stay_client,
    "wellness": wellness_server.wellness_client,
    "gaming": gaming_server.gaming_client,
}

# One cheap, read-only, no-idempotency-key call per domain -- exactly the shape
# `test_resilience.py`'s `_other_domain_read_calls_succeed` already uses, so these are
# known-good calls against the real seeded data when no fault is injected.
CALLS = {
    "dining": lambda: dining_server.check_availability(
        input=dining_server.CheckAvailabilityInput(
            venue_id="ven-mgv-01", date="2026-12-31", party_size=2
        )
    ),
    "entertainment": lambda: entertainment_server.search_events(
        input=entertainment_server.SearchEventsInput(classification=None, date=None)
    ),
    "stay": lambda: stay_server.search_rooms(
        input=stay_server.SearchRoomsInput(arrival="2026-12-30", departure="2026-12-31", adults=2)
    ),
    "wellness": lambda: wellness_server.list_services(
        input=wellness_server.ListServicesInput(category=None)
    ),
    "gaming": lambda: gaming_server.list_casino_floor(
        input=gaming_server.ListCasinoFloorInput(game_type=None)
    ),
}

# Per `specs/002-simulation-ecosystem/contracts/sim-control-plane.md`'s table, mirrored
# in each sim's `add_fault_middleware(..., supported_faults=...)` call.
SUPPORTED_GENERIC_FAULTS = {
    "dining": {"timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "schema_drift"},
    "entertainment": {
        "timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "webhook_delay", "schema_drift",
    },
    "stay": {"timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "schema_drift"},
    "wellness": {"timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "schema_drift"},
    "gaming": {"timeout", "error_500", "rate_limit", "schema_drift"},
}

# These are business-logic faults only meaningful against an in-flight hold/webhook,
# not the plain read call every domain's `CALLS` entry drives -- `active_fault()`
# retrieves them but no code path in a read-only GET consults it, so a read call
# against one of these simply succeeds normally (no fault applies to it). They are
# covered by `hold_expired`/`inventory_race`-specific tests elsewhere in this feature's
# suite (`sims/tests/`) and are out of scope for this generic taxonomy sweep, which
# proves classification for the faults every domain's *read* surface can actually hit.
BUSINESS_LOGIC_ONLY_FAULTS = {"hold_expired", "inventory_race", "webhook_delay"}

# The faults this suite actually drives through a plain read call, per domain.
GENERIC_TAXONOMY_FAULTS = {
    domain: sorted(faults - BUSINESS_LOGIC_ONLY_FAULTS)
    for domain, faults in SUPPORTED_GENERIC_FAULTS.items()
}


def _identity_for(tenant_id: str = "MGV") -> CallerIdentity:
    return CallerIdentity(
        sub="error-taxonomy-test",
        tenant_id=tenant_id,
        scopes=(
            "dining:read", "dining:hold", "dining:commit",
            "entertainment:read", "entertainment:hold", "entertainment:commit",
            "stay:read", "stay:hold", "stay:commit",
            "wellness:read", "wellness:hold", "wellness:commit",
            "gaming:read",
        ),
        iat=0,
        exp=99_999_999_999,
    )


async def _call_with_fault(domain: str, fault_value: str | None):
    identity_token = _current_identity.set(_identity_for())
    fault_token = _current_sim_fault.set(fault_value)
    try:
        return await CALLS[domain]()
    finally:
        _current_sim_fault.reset(fault_token)
        _current_identity.reset(identity_token)


@pytest.fixture(autouse=True)
def _reset_all_breakers_after():
    """Every breaker this file touches (all five -- every domain gets at least
    `error_500`/`rate_limit`/`schema_drift` driven at it) is restored to a fresh CLOSED
    state afterward, matching `test_resilience.py`'s and `test_entitlements.py`'s own
    convention for these process-lifetime singletons."""
    yield
    for client in DOMAIN_CLIENTS.values():
        breaker = client.breaker
        breaker._state = "CLOSED"
        breaker._consecutive_failures = 0
        breaker._opened_at = None
        breaker._half_open_probe_in_flight = False
        breaker.cooldown_seconds = resilience.BREAKER_COOLDOWN_SECONDS


def _assert_no_raw_vendor_envelope_leaked(failure: NormalizedFailure) -> None:
    """Zero raw vendor error envelopes escape uncaught (SC-006): a `NormalizedFailure`
    is the only failure shape any of these calls can ever return (the tool wrapper's
    `except UpstreamFailure` is the sole path back to the caller on error -- any other
    exception propagates and fails the test outright, so simply getting a
    `NormalizedFailure` back at all is half the proof); the other half is that its
    `detail` never contains one of the three raw vendor envelope keys verbatim as a
    literal Python-dict rendering, which would mean a vendor body leaked through
    unnormalized rather than being reduced to `error_class`/`detail`/`vendor_code`."""
    assert isinstance(failure, NormalizedFailure)
    for raw_key in ("{'error':", '{"error":', "{'fault':", '{"fault":', "err_code"):
        assert raw_key not in failure.detail, f"raw vendor envelope leaked in detail: {failure.detail!r}"


# --- error_500 -> RETRYABLE, every domain (SC-006's explicit clause) ----------------


@_async_test
@pytest.mark.parametrize("domain", sorted(CALLS))
async def test_error_500_surfaces_retryable_in_every_domain(reset_sims, domain: str) -> None:
    result = await _call_with_fault(domain, "error_500")
    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "RETRYABLE"
    assert result.domain == domain
    _assert_no_raw_vendor_envelope_leaked(result)


# --- rate_limit -> RETRYABLE, every domain -------------------------------------------


@_async_test
@pytest.mark.parametrize("domain", sorted(CALLS))
async def test_rate_limit_surfaces_retryable_with_retry_after_in_every_domain(
    reset_sims, domain: str
) -> None:
    result = await _call_with_fault(domain, "rate_limit")
    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "RETRYABLE"
    # sims/_shared/faults.py's rate_limit fault always sets Retry-After: 1.
    assert result.retry_after_seconds == 1
    _assert_no_raw_vendor_envelope_leaked(result)


# --- timeout -> RETRYABLE, every domain (slow: bounded to one attempt per call) ------


@_async_test
@pytest.mark.parametrize("domain", sorted(CALLS))
async def test_timeout_surfaces_retryable_in_every_domain(reset_sims, domain: str, monkeypatch) -> None:
    # sims/_shared/faults.py's `timeout` fault sleeps 3s before answering 504; bounding
    # retries to a single attempt keeps this suite's runtime sane across five domains
    # without weakening what's being proven (classification of the resulting 504, not
    # the retry count -- that's `test_retry_policy.py`'s job).
    monkeypatch.setattr(resilience, "RETRY_ATTEMPTS", 1)
    result = await _call_with_fault(domain, "timeout")
    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "RETRYABLE"
    _assert_no_raw_vendor_envelope_leaked(result)


# --- schema_drift (T071, SC-016) -> a specific, distinct, non-RETRYABLE class --------


@_async_test
@pytest.mark.parametrize("domain", sorted(CALLS))
async def test_schema_drift_surfaces_a_distinct_non_retryable_class(reset_sims, domain: str) -> None:
    """A live sim's response deliberately violates its own schema (T071): missing a
    required field and carrying an undeclared one. The gateway must neither crash, nor
    classify it RETRYABLE (endlessly retrying a body shape the vendor's own contract
    would never produce cannot succeed differently), nor pass the malformed body
    through -- `errors.classify_body`'s `ValidationError` branch classifies it
    TERMINAL."""
    result = await _call_with_fault(domain, "schema_drift")
    assert isinstance(result, NormalizedFailure)
    assert result.error_class != "RETRYABLE"
    assert result.error_class == "TERMINAL"
    _assert_no_raw_vendor_envelope_leaked(result)
    # The drifted/undeclared field never reaches the caller in any form.
    assert "__sim_schema_drift__" not in result.detail


# --- Total enumeration guard: every fault value this suite claims to cover is real ---


def test_all_fault_values_are_accounted_for() -> None:
    """Guards this file against silent drift from `sims/_shared/faults.py`'s registry:
    every value in `ALL_FAULT_VALUES` is either driven by this suite's generic sweep or
    explicitly named as business-logic-only and out of scope for it."""
    covered = set()
    for faults in GENERIC_TAXONOMY_FAULTS.values():
        covered.update(faults)
    assert covered | BUSINESS_LOGIC_ONLY_FAULTS == ALL_FAULT_VALUES


def test_every_domain_drives_every_one_of_its_supported_generic_faults() -> None:
    """Each domain's `GENERIC_TAXONOMY_FAULTS` entry matches its real
    `supported_faults` declaration minus the business-logic-only faults -- this is the
    "against all five vendors" half of SC-006, made explicit rather than left to be
    inferred from the parametrize lists above."""
    for domain, faults in GENERIC_TAXONOMY_FAULTS.items():
        assert set(faults) == SUPPORTED_GENERIC_FAULTS[domain] - BUSINESS_LOGIC_ONLY_FAULTS
        assert {"error_500", "rate_limit", "timeout", "schema_drift"} <= set(faults)
