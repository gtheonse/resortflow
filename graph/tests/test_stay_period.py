"""Feature 011, T014 — `graph.bookings.stay_period` (research R-04, FR-019): "my stay" is the arrival and
departure of the guest's held or confirmed room in this conversation; never text-derived, never assumed.

Must fail before T015 (`BookingRecord.ends_at`, `stay_period`)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from graph.bookings import booking_view, stay_period
from graph.models import Confirmation, Domain, Hold, HoldStatus, Proposal


def _stay_proposal(key: str, arrival: date, departure: date, **kw) -> Proposal:
    return Proposal(
        proposal_id=f"stay:{key}", domain=Domain.STAY, tenant_id="MGV", title="Standard Queen Room",
        starts_at=datetime(arrival.year, arrival.month, arrival.day, tzinfo=UTC),
        ends_at=datetime(departure.year, departure.month, departure.day, tzinfo=UTC),
        party_size=2, hold_idempotency_key=key, **kw,
    )


def _hold(key: str, status: HoldStatus, domain: Domain = Domain.STAY) -> Hold:
    return Hold(
        idempotency_key=key, hold_id=f"h-{key}", domain=domain, tenant_id="MGV", saga_id="s",
        status=status, expires_at=datetime(2026, 10, 1, tzinfo=UTC), proposal_id=f"stay:{key}",
    )


def _confirmation(key: str, domain: Domain = Domain.STAY) -> Confirmation:
    return Confirmation(confirmation_id=f"C-{key}", idempotency_key=key, domain=domain, confirmed_at=datetime(2026, 9, 30, tzinfo=UTC))


def _state(*, proposals, holds, confirmations=()) -> dict:
    return {"proposals": list(proposals), "holds": list(holds), "confirmations": list(confirmations)}


def test_held_stay_gives_its_arrival_and_departure() -> None:
    state = _state(proposals=[_stay_proposal("k1", date(2026, 10, 5), date(2026, 10, 8))], holds=[_hold("k1", HoldStatus.HELD)])
    record = booking_view(state)[0]
    assert record.ends_at is not None and record.ends_at.date() == date(2026, 10, 8)
    assert stay_period(booking_view(state)) == (date(2026, 10, 5), date(2026, 10, 8))


def test_confirmed_stay_counts() -> None:
    state = _state(
        proposals=[_stay_proposal("k1", date(2026, 10, 5), date(2026, 10, 8))],
        holds=[_hold("k1", HoldStatus.CONFIRMED)], confirmations=[_confirmation("k1")],
    )
    assert stay_period(booking_view(state)) == (date(2026, 10, 5), date(2026, 10, 8))


def test_released_expired_or_absent_stay_gives_none() -> None:
    for status in (HoldStatus.RELEASED, HoldStatus.EXPIRED, HoldStatus.ORPHAN_RELEASED):
        state = _state(proposals=[_stay_proposal("k1", date(2026, 10, 5), date(2026, 10, 8))], holds=[_hold("k1", status)])
        assert stay_period(booking_view(state)) is None, status
    assert stay_period(booking_view(_state(proposals=[], holds=[]))) is None


def test_non_stay_records_are_ignored() -> None:
    dining = Proposal(
        proposal_id="d1", domain=Domain.DINING, tenant_id="MGV", title="Steakhouse",
        starts_at=datetime(2026, 10, 5, 19, tzinfo=UTC), party_size=2, hold_idempotency_key="kd",
    )
    state = _state(proposals=[dining], holds=[_hold("kd", HoldStatus.HELD, Domain.DINING)])
    assert stay_period(booking_view(state)) is None
    assert booking_view(state)[0].ends_at is None


def test_latest_active_stay_wins_when_there_are_several() -> None:
    state = _state(
        proposals=[
            _stay_proposal("k1", date(2026, 10, 5), date(2026, 10, 8)),
            _stay_proposal("k2", date(2026, 11, 1), date(2026, 11, 3)),
        ],
        holds=[_hold("k1", HoldStatus.HELD), _hold("k2", HoldStatus.HELD)],
    )
    assert stay_period(booking_view(state)) == (date(2026, 11, 1), date(2026, 11, 3))


def test_a_later_released_stay_does_not_mask_an_earlier_active_one() -> None:
    state = _state(
        proposals=[
            _stay_proposal("k1", date(2026, 10, 5), date(2026, 10, 8)),
            _stay_proposal("k2", date(2026, 11, 1), date(2026, 11, 3)),
        ],
        holds=[_hold("k1", HoldStatus.HELD), _hold("k2", HoldStatus.RELEASED)],
    )
    assert stay_period(booking_view(state)) == (date(2026, 10, 5), date(2026, 10, 8))


def test_duplicated_confirmations_do_not_change_the_answer() -> None:
    state = _state(
        proposals=[_stay_proposal("k1", date(2026, 10, 5), date(2026, 10, 8))],
        holds=[_hold("k1", HoldStatus.CONFIRMED)],
        confirmations=[_confirmation("k1"), _confirmation("k1")],
    )
    assert stay_period(booking_view(state)) == (date(2026, 10, 5), date(2026, 10, 8))


def test_a_stay_with_no_dates_gives_none() -> None:
    proposal = Proposal(
        proposal_id="s1", domain=Domain.STAY, tenant_id="MGV", title="Room", party_size=2, hold_idempotency_key="k1",
    )
    assert stay_period(booking_view(_state(proposals=[proposal], holds=[_hold("k1", HoldStatus.HELD)]))) is None
