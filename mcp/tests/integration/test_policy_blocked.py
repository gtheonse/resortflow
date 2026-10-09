"""T072 [CRIT] -- self-exclusion integration test (the most regulator-relevant path in
this feature): `resortflow.gaming.get_loyalty_status` for a seeded `self_excluded` guest must
surface `POLICY_BLOCKED` in both tenants, proven against the live casino-sim rather than
a mocked 403.

`sims/_shared/guest_seed.py` seeds the first two guests per tenant (`guest-mgv-001`,
`guest-mhb-001`) as `self_excluded=True` with `offers_permitted=False`
(`sims/casino/seed.py` writes that into the `responsible_gaming` table); casino-sim's
real `GET /casino/v1/loyalty/{guest_id}/offers` handler
(`sims/casino/main.py::get_loyalty_offers`) raises a real `403` for exactly that guest.
`gateway._shared.errors.classify_status`'s gaming+403 branch classifies that as
`POLICY_BLOCKED` (never retryable -- FR-019), and `gateway.gaming.server.get_loyalty_status`
re-raises rather than swallowing it as an optional-enrichment miss, so the tool call as a
whole fails `POLICY_BLOCKED` with no offers payload at all.

Called in-process (`gateway.gaming.server.get_loyalty_status` directly, with a
`CallerIdentity` published on the same context var the real auth middleware would use)
rather than over the MCP wire transport -- `test_indeterminate_commit.py` already
documents the reason: the MCP SDK's typed-return validation chokes when a tool declared
to return a success model (here, `LoyaltyProfile`) actually returns a `NormalizedFailure`,
an unrelated pre-existing gap. Calling the registered tool wrapper directly still drives a
real HTTP round-trip to the real, live casino-sim (`reset_sims`/`live_stack` from
`conftest.py`) through the real `UpstreamClient` and `errors.classify_status` -- everything
this test needs to prove is live, none of it is mocked.
"""

from __future__ import annotations

import pytest
from gateway._shared.errors import NormalizedFailure
from gateway._shared.identity import CallerIdentity
from gateway._shared.middleware import _current_identity
from gateway.gaming import server as gaming_server
from gateway.gaming.server import GetLoyaltyStatusInput

pytestmark = pytest.mark.asyncio(loop_scope="module")
# `loop_scope="module"` (not the pytest-asyncio default of function-scoped): this file
# calls `gateway.gaming.server.get_loyalty_status` directly (bypassing the MCP wire
# transport, per this module's own docstring), which reuses `gaming_server.gaming_client`
# -- a module-level `UpstreamClient` holding one `httpx.AsyncClient` whose connection pool
# binds to whichever event loop first uses it. A fresh per-test event loop (the default)
# breaks that pool on the second test with "RuntimeError: Event loop is closed" during
# teardown of the first connection. `test_resilience.py`/`test_indeterminate_commit.py`
# never hit this because they either drive calls through the wire transport (which
# executes gateway-internal async code inside `live_stack`'s own background-thread event
# loop, not pytest's) or contain only a single async test each.

# Seeded self-excluded guests (`sims/_shared/guest_seed.py`: the first two guests per
# tenant, `i < 2`, are always self-excluded -- guaranteed >= 1 per tenant).
SELF_EXCLUDED_GUEST = {"MGV": "guest-mgv-001", "MHB": "guest-mhb-001"}

# A guest deliberately not among the seeded self-excluded set, used as a same-tenant
# control to prove the block is guest-specific rather than a blanket casino-sim failure.
NOT_SELF_EXCLUDED_GUEST = {"MGV": "guest-mgv-010", "MHB": "guest-mhb-010"}


def _set_gaming_identity(tenant_id: str) -> object:
    identity = CallerIdentity(
        sub="policy-blocked-test",
        tenant_id=tenant_id,
        scopes=("gaming:read",),
        iat=0,
        exp=99_999_999_999,
    )
    return _current_identity.set(identity)


@pytest.fixture(autouse=True)
def _reset_gaming_breaker_after():
    """The gaming `UpstreamClient.breaker` is a process-lifetime singleton shared with
    every other test file in this session -- a POLICY_BLOCKED response never trips it
    (`UpstreamClient.call`'s "determinate business rejection... does not trip the
    breaker" branch), but this keeps the invariant explicit and cheap regardless."""
    yield
    breaker = gaming_server.gaming_client.breaker
    breaker._state = "CLOSED"
    breaker._consecutive_failures = 0
    breaker._opened_at = None
    breaker._half_open_probe_in_flight = False


@pytest.mark.parametrize("tenant_id", ["MGV", "MHB"])
async def test_self_excluded_guest_surfaces_policy_blocked(reset_sims, tenant_id: str) -> None:
    guest_id = SELF_EXCLUDED_GUEST[tenant_id]
    token = _set_gaming_identity(tenant_id)
    try:
        result = await gaming_server.get_loyalty_status(input=GetLoyaltyStatusInput(guest_id=guest_id))
    finally:
        _current_identity.reset(token)

    assert isinstance(result, NormalizedFailure), (
        f"expected a POLICY_BLOCKED NormalizedFailure for {tenant_id}/{guest_id}, "
        f"got a successful {type(result).__name__} instead -- no offers payload should "
        f"ever be reachable for a self-excluded guest"
    )
    assert result.error_class == "POLICY_BLOCKED"
    assert result.error_class != "RETRYABLE"
    assert result.domain == "gaming"
    # No offers payload of any kind is present anywhere on the failure envelope.
    assert not hasattr(result, "offers")


@pytest.mark.parametrize("tenant_id", ["MGV", "MHB"])
async def test_non_self_excluded_guest_in_same_tenant_is_not_blocked(
    reset_sims, tenant_id: str
) -> None:
    """Control: the block is specific to the self-excluded guest, not a blanket
    casino-sim/tenant failure -- an ordinary guest in the same tenant succeeds and gets
    back a real `LoyaltyProfile`."""
    from gateway.gaming.models import LoyaltyProfile

    guest_id = NOT_SELF_EXCLUDED_GUEST[tenant_id]
    token = _set_gaming_identity(tenant_id)
    try:
        result = await gaming_server.get_loyalty_status(input=GetLoyaltyStatusInput(guest_id=guest_id))
    finally:
        _current_identity.reset(token)

    assert isinstance(result, LoyaltyProfile)


async def test_self_exclusion_block_holds_in_both_tenants_independently(reset_sims) -> None:
    """Both tenants' seeded self-excluded guest are independently blocked -- proves this
    isn't a fixture artifact of a single tenant's data or a single identity context."""
    for tenant_id, guest_id in SELF_EXCLUDED_GUEST.items():
        token = _set_gaming_identity(tenant_id)
        try:
            result = await gaming_server.get_loyalty_status(
                input=GetLoyaltyStatusInput(guest_id=guest_id)
            )
        finally:
            _current_identity.reset(token)
        assert isinstance(result, NormalizedFailure), f"tenant {tenant_id} guest {guest_id} was not blocked"
        assert result.error_class == "POLICY_BLOCKED", f"tenant {tenant_id} guest {guest_id} was not blocked"
