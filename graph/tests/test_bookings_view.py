"""Feature 010, T018 — `graph.bookings.booking_view` (data-model.md
`BookingRecord`, research R-12): joins `proposals`/`holds`/`confirmations`
on `idempotency_key`, **deduplicated by that key** — the property that
matters most, since today's `_deterministic_core` copy-back bug (fixed at
T052) writes exactly the kind of duplicated confirmation this test guards
against.

Must fail before T019 (`graph.bookings.BookingRecord`/`booking_view`)."""

from __future__ import annotations

from datetime import UTC, datetime

from graph.bookings import booking_view
from graph.models import Confirmation, Domain, Hold, HoldStatus, Proposal


def _proposal(**overrides) -> Proposal:
    base = {
        "proposal_id": "prop-1",
        "domain": Domain.DINING,
        "tenant_id": "MGV",
        "title": "Silverline Chophouse",
        "starts_at": datetime(2026, 9, 30, 20, 0, tzinfo=UTC),
        "party_size": 4,
        "hold_idempotency_key": "resortflow:sess:prop-1:1",
    }
    base.update(overrides)
    return Proposal(**base)


def _hold(**overrides) -> Hold:
    base = {
        "idempotency_key": "resortflow:sess:prop-1:1",
        "hold_id": "hold-1",
        "domain": Domain.DINING,
        "tenant_id": "MGV",
        "saga_id": "saga-1",
        "status": HoldStatus.HELD,
        "expires_at": datetime(2026, 9, 30, 19, 45, tzinfo=UTC),
        "proposal_id": "prop-1",
    }
    base.update(overrides)
    return Hold(**base)


def _confirmation(**overrides) -> Confirmation:
    base = {
        "confirmation_id": "ABC12345",
        "idempotency_key": "resortflow:sess:prop-1:1",
        "domain": Domain.DINING,
        "confirmed_at": datetime(2026, 9, 30, 18, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return Confirmation(**base)


def test_held_booking_has_no_confirmation_and_reports_hold_status() -> None:
    state = {"proposals": [_proposal()], "holds": [_hold()], "confirmations": []}
    records = booking_view(state)
    assert len(records) == 1
    record = records[0]
    assert record.key == "resortflow:sess:prop-1:1"
    assert record.venue == "Silverline Chophouse"
    assert record.status == "held"
    assert record.confirmation_code is None
    assert record.expires_at is not None


def test_confirmed_booking_reports_confirmed_status_and_code() -> None:
    state = {
        "proposals": [_proposal()],
        "holds": [_hold(status=HoldStatus.CONFIRMED)],
        "confirmations": [_confirmation()],
    }
    (record,) = booking_view(state)
    assert record.status == "confirmed"
    assert record.confirmation_code == "ABC12345"


def test_terminal_hold_never_reports_an_expires_at() -> None:
    """FR-034: a countdown must never be shown for a finished hold."""
    for status in (HoldStatus.CONFIRMED, HoldStatus.RELEASED, HoldStatus.EXPIRED, HoldStatus.ORPHAN_RELEASED):
        state = {
            "proposals": [_proposal()],
            "holds": [_hold(status=status)],
            "confirmations": [_confirmation()] if status == HoldStatus.CONFIRMED else [],
        }
        (record,) = booking_view(state)
        assert record.expires_at is None, f"status={status} must not carry an expiry"


def test_duplicated_confirmation_collapses_to_one_record() -> None:
    """The exact shape of today's copy-back bug (research R-12): the same
    booking's confirmation appended twice to the (append-only) channel."""
    state = {
        "proposals": [_proposal()],
        "holds": [_hold(status=HoldStatus.CONFIRMED)],
        "confirmations": [_confirmation(), _confirmation()],
    }
    records = booking_view(state)
    assert len(records) == 1
    assert records[0].confirmation_code == "ABC12345"


def test_two_distinct_bookings_produce_two_records() -> None:
    state = {
        "proposals": [_proposal(), _proposal(proposal_id="prop-2", title="Sky Lounge", hold_idempotency_key="resortflow:sess:prop-2:1")],
        "holds": [_hold(), _hold(idempotency_key="resortflow:sess:prop-2:1", hold_id="hold-2", proposal_id="prop-2")],
        "confirmations": [],
    }
    records = booking_view(state)
    assert {r.key for r in records} == {"resortflow:sess:prop-1:1", "resortflow:sess:prop-2:1"}


def test_empty_state_gives_no_records() -> None:
    assert booking_view({"proposals": [], "holds": [], "confirmations": []}) == []
