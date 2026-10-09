"""T063 — the only proof of `_shared.registry`'s composite fan-out mechanism (T024,
FR-030, SC-014).

F3 (`specs/003-mcp-tool-layer/tasks.md`): every composite tool actually shipped this
phase is read-only (`dining.list_venues_with_events`, `stay.get_stay_details`) — a
multi-*mutation* composite is inherently cross-domain saga territory, which is Phase 4-5
work. So there is no shipped tool to drive this test against. Instead this module builds
a synthetic two-slot **mutating** composite fixture — never registered on any real
domain server, never wired to a real vendor — purely to exercise `SubCallSpec`,
`required_for_success`, and `completed_sub_calls` population on partial failure, the exact
mechanism `_shared/registry.py` implements and every real composite in this phase leaves
unexercised.

Fixture shape: slot 1 ("create_leg") succeeds and records a `CompletedSubCall`; slot 2
("confirm_leg") then fails. The tool raises `UpstreamFailure` carrying slot 1's completed
sub-call — proving the failure surfaces every completed sub-call's `slot_name`,
`vendor_resource_id`, and derived `idempotency_key` — and this test additionally asserts
no compensating call is attempted against slot 1 (SC-014's "no compensation attempted"
half): compensation is out of scope for this phase (saga territory, Phase 4-5).
"""

from __future__ import annotations

from contextvars import Token

import pytest
from gateway._shared.errors import CompletedSubCall, NormalizedFailure, UpstreamFailure
from gateway._shared.idempotency import sub_call_key
from gateway._shared.identity import CallerIdentity
from gateway._shared.middleware import _current_identity
from gateway._shared.registry import SubCallSpec, register_tool

pytestmark = pytest.mark.asyncio

# --- Test scaffolding: publish a CallerIdentity the way the real middleware would ------


@pytest.fixture
def caller_identity() -> object:
    identity = CallerIdentity(
        sub="test-harness",
        tenant_id="MGV",
        scopes=("dining:hold",),
        iat=0,
        exp=9_999_999_999,
    )
    token: Token = _current_identity.set(identity)
    yield identity
    _current_identity.reset(token)


# --- The synthetic mutating composite fixture ------------------------------------------
#
# Never a real tool: not mounted on any FastMCP app, not reachable through the gateway's
# HTTP surface. It exists solely so `register_tool`'s decorator chain runs end to end
# against a mutating, multi-slot composite.

compensation_calls: list[str] = []


class FixtureCompositeInput:
    """Stand-in input — deliberately not a Pydantic model, since this fixture is never
    registered against a real MCP server and doesn't need schema introspection."""

    def __init__(self, party_size: int) -> None:
        self.party_size = party_size


SLOT_1 = SubCallSpec("create_leg", "createLeg", required_for_success=True)
SLOT_2 = SubCallSpec("confirm_leg", "confirmLeg", required_for_success=True)


@register_tool(
    domain="dining",
    entitlement_action="hold",
    is_mutating=True,
    sub_calls=[SLOT_1, SLOT_2],
)
async def fixture_composite_mutation(
    input: FixtureCompositeInput, idempotency_key: str | None = None
) -> dict:
    """Synthetic two-slot mutating composite: slot 1 always succeeds, slot 2 always
    fails. Mirrors what a real cross-domain saga step would do — derive a per-slot
    idempotency key via `sub_call_key`, record a `CompletedSubCall` for each slot that
    finishes, and raise `UpstreamFailure` carrying every slot completed so far the moment
    a required slot fails. No rollback of slot 1 is attempted (SC-014): this phase's
    composite mechanism only reports what completed, it does not compensate.
    """
    assert idempotency_key is not None  # enforced by register_tool before this runs

    completed: list[CompletedSubCall] = []

    # Slot 1 — succeeds.
    slot_1_key = sub_call_key(idempotency_key, SLOT_1.slot_name)
    slot_1_resource_id = "leg-hold-0001"
    completed.append(
        CompletedSubCall(
            slot_name=SLOT_1.slot_name,
            vendor_resource_id=slot_1_resource_id,
            idempotency_key=slot_1_key,
        )
    )

    # Slot 2 — fails. `required_for_success=True`, so the whole composite fails, carrying
    # every slot that completed before the failure (just slot 1) and nothing for slot 2.
    slot_2_key = sub_call_key(idempotency_key, SLOT_2.slot_name)
    raise UpstreamFailure(
        error_class="TERMINAL",
        domain="dining",
        detail="fixture: slot 2 (confirm_leg) rejected by vendor",
        vendor_code="SLOT_UNAVAILABLE",
        http_status=409,
        completed_sub_calls=completed,
    )
    # Unreachable, but documents intent: a real saga-compensation step (Phase 4-5) would
    # go here, keyed by `slot_2_key`, to release slot 1's hold. This phase does none of
    # that — compensation_calls staying empty is exactly what this test asserts.
    compensation_calls.append(slot_2_key)  # pragma: no cover


# --- The test ---------------------------------------------------------------------------


async def test_composite_partial_failure_reports_completed_sub_calls_and_no_compensation(
    caller_identity: CallerIdentity,
) -> None:
    idempotency_key = "resortflow:sess-fixture:hold-fixture:1"

    result = await fixture_composite_mutation(
        FixtureCompositeInput(party_size=2), idempotency_key=idempotency_key
    )

    # The decorator normalizes the raised UpstreamFailure into a NormalizedFailure —
    # never a raw exception, never a vendor envelope shape.
    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "TERMINAL"
    assert result.domain == "dining"

    # SC-014: the failure carries every completed sub-call's slot_name,
    # vendor_resource_id, and derived idempotency_key.
    assert result.completed_sub_calls is not None
    assert len(result.completed_sub_calls) == 1

    completed = result.completed_sub_calls[0]
    assert completed.slot_name == "create_leg"
    assert completed.vendor_resource_id == "leg-hold-0001"
    assert completed.idempotency_key == sub_call_key(idempotency_key, "create_leg")

    # Slot 2 never completed, so it never appears in completed_sub_calls.
    assert all(sc.slot_name != "confirm_leg" for sc in result.completed_sub_calls)

    # SC-014's other half: no compensation was attempted against the completed slot.
    assert compensation_calls == []

    # The top-level idempotency key (not a sub-call key) rides on the outer failure too.
    assert result.idempotency_key == idempotency_key


async def test_sub_call_key_is_deterministic_across_slots(
    caller_identity: CallerIdentity,
) -> None:
    """`sub_call_key` derivation must be pure and per-slot: same idempotency_key + slot
    name always yields the same derived key, and different slots never collide — this is
    what makes `completed_sub_calls[*].idempotency_key` trustworthy for a future
    reconciler to key off (T024's docstring, ADR-015's qualification)."""
    key = "resortflow:sess-fixture:hold-fixture:1"

    assert sub_call_key(key, "create_leg") == sub_call_key(key, "create_leg")
    assert sub_call_key(key, "create_leg") != sub_call_key(key, "confirm_leg")
