"""Contract every `SagaParticipant` must honour (ADR-061) — the Liskov guarantee
the supervisor relies on when it treats all domains uniformly."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from graph.domains import default_registry
from graph.models import Domain, Hold, HoldStatus
from graph.participants import SagaParticipant, StandaloneDomain

REGISTRY = default_registry()
MUTATING = [Domain.DINING, Domain.ENTERTAINMENT, Domain.WELLNESS, Domain.STAY]

# The vendor verb each participant must translate `release` into (frozen MCP vocabulary).
RELEASE_VERB = {
    Domain.DINING: "release_hold",
    Domain.ENTERTAINMENT: "release_seat_hold",
    Domain.WELLNESS: "release_amenity_hold",
    Domain.STAY: "release_room_hold",
}


class _RecordingTools:
    """Accepts any vendor verb and records the call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        async def _call(**kwargs: Any) -> None:
            self.calls.append((name, kwargs))

        return _call


def _hold(domain: Domain) -> Hold:
    return Hold(
        idempotency_key="k1", hold_id="h1", domain=domain, tenant_id="MGV", saga_id="s1",
        status=HoldStatus.HELD, expires_at=datetime(2030, 1, 1, tzinfo=UTC), proposal_id="p1",
    )


def test_every_domain_is_registered_for_standalone_dispatch() -> None:
    for domain in Domain:
        assert isinstance(REGISTRY.standalone(domain), StandaloneDomain), domain


def test_gaming_is_read_only_and_cannot_be_a_saga_participant() -> None:
    assert REGISTRY.participant(Domain.GAMING) is None
    assert Domain.GAMING not in REGISTRY.fanout_domains()
    assert Domain.GAMING not in REGISTRY.commit_order()


def test_fanout_order_is_registration_order_and_commit_order_is_by_rank() -> None:
    # Fan-out insertion order fixes the append order of parallel writes — it must not change.
    assert REGISTRY.fanout_domains() == (Domain.DINING, Domain.ENTERTAINMENT, Domain.WELLNESS, Domain.STAY)
    # research.md: least flexible first — entertainment, dining, wellness, then stay.
    assert REGISTRY.commit_order() == (Domain.ENTERTAINMENT, Domain.DINING, Domain.WELLNESS, Domain.STAY)


@pytest.mark.parametrize("domain", MUTATING)
def test_mutating_domains_implement_the_full_saga_contract(domain: Domain) -> None:
    participant = REGISTRY.participant(domain)
    assert isinstance(participant, SagaParticipant)
    assert participant.domain is domain


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", MUTATING)
async def test_release_translates_to_the_vendor_verb_with_a_derived_idempotency_key(domain: Domain) -> None:
    participant = REGISTRY.participant(domain)
    assert participant is not None
    tools = _RecordingTools()
    await participant.release(tools, _hold(domain))
    assert tools.calls == [(RELEASE_VERB[domain], {"hold_id": "h1", "idempotency_key": "k1:release"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", MUTATING)
async def test_find_alternative_tolerates_a_missing_failed_proposal(domain: Domain) -> None:
    participant = REGISTRY.participant(domain)
    assert participant is not None
    assert await participant.find_alternative(_RecordingTools(), None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", [Domain.DINING, Domain.WELLNESS, Domain.STAY])
async def test_verify_reports_no_independent_check_as_none_never_as_a_pass(domain: Domain) -> None:
    participant = REGISTRY.participant(domain)
    assert participant is not None
    # `None` (not `True`) is how a disclosed verification gap stays visible to the saga.
    assert await participant.verify(_RecordingTools(), object()) is None  # type: ignore[arg-type]
