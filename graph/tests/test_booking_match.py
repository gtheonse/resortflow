"""Feature 010, T022 — `graph.bookings.match_bookings` (analyze finding I1:
moved to Foundational since US1's follow-ups, US2's lookup, and US4's
modify hand-off all need the same matcher). Pure function, no I/O. Must
fail before T023."""

from __future__ import annotations

from datetime import UTC, date, datetime

from graph.bookings import BookingRecord, match_bookings
from graph.models import Domain

_TODAY = date(2026, 9, 27)


def _record(**overrides) -> BookingRecord:
    base = {
        "key": "k1",
        "service": Domain.DINING,
        "venue": "Silverline Chophouse",
        "starts_at": datetime(2026, 9, 30, 20, 0, tzinfo=UTC),
        "party_size": 4,
        "status": "confirmed",
        "hold_id": "hold-1",
        "confirmation_code": "ABC12345",
    }
    base.update(overrides)
    return BookingRecord(**base)


def test_matches_by_service_and_verbatim_date_time() -> None:
    records = [_record()]
    matched = match_bookings(
        records,
        services=[Domain.DINING],
        text="what is the restaurent i got reserved for sep 30, 8 PM?",
        today=_TODAY,
    )
    assert matched == [records[0]]


def test_date_that_matches_nothing_returns_empty() -> None:
    records = [_record()]
    matched = match_bookings(records, services=[Domain.DINING], text="what about oct 1?", today=_TODAY)
    assert matched == []


def test_no_service_given_matches_by_date_alone() -> None:
    records = [_record(), _record(key="k2", service=Domain.WELLNESS, venue="Spa", starts_at=datetime(2026, 10, 5, 14, 0, tzinfo=UTC))]
    matched = match_bookings(records, services=[], text="what did I book for sep 30?", today=_TODAY)
    assert [r.key for r in matched] == ["k1"]


def test_no_date_or_service_given_returns_all_records() -> None:
    records = [_record(), _record(key="k2", service=Domain.WELLNESS, venue="Spa")]
    matched = match_bookings(records, services=[], text="what have I booked?", today=_TODAY)
    assert {r.key for r in matched} == {"k1", "k2"}


def test_service_given_with_no_date_filters_by_service_only() -> None:
    records = [_record(), _record(key="k2", service=Domain.WELLNESS, venue="Spa")]
    matched = match_bookings(records, services=[Domain.WELLNESS], text="what's my spa booking?", today=_TODAY)
    assert [r.key for r in matched] == ["k2"]
