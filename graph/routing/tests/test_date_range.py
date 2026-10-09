"""Feature 011, T012 — relative and ranged date phrases
(`specs/011-browse-requests/contracts/browse-router-api.md`, `graph.routing.temporal`).

Fixed clock: 2026-09-30 is a Wednesday, so "this weekend" is Sat 2026-10-03 to Sun 2026-10-04."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from graph.routing.temporal import parse_date_range, parse_stay_length

TODAY = date(2026, 9, 30)  # Wednesday


@pytest.mark.parametrize(
    "text,offset,expected",
    [
        ("what events are happening in the next 5 days", 0, (date(2026, 9, 30), date(2026, 10, 4))),
        ("book stay for next 5 days", 1, (date(2026, 10, 1), date(2026, 10, 5))),
        ("any shows this weekend", 0, (date(2026, 10, 3), date(2026, 10, 4))),
        ("what's on next weekend", 0, (date(2026, 10, 10), date(2026, 10, 11))),
        ("how about next week", 0, (date(2026, 10, 5), date(2026, 10, 11))),
        ("what rooms are available oct 5 to oct 8", 0, (date(2026, 10, 5), date(2026, 10, 8))),
        ("events from oct 5 to oct 8", 0, (date(2026, 10, 5), date(2026, 10, 8))),
        ("concerts between oct 5 and oct 8", 0, (date(2026, 10, 5), date(2026, 10, 8))),
        ("shows oct 5 to 8", 0, (date(2026, 10, 5), date(2026, 10, 8))),
        ("events from dec 30 to jan 2", 0, (date(2026, 12, 30), date(2027, 1, 2))),
        ("events tonight", 0, (date(2026, 9, 30), date(2026, 9, 30))),
        ("events tomorrow", 0, (date(2026, 10, 1), date(2026, 10, 1))),
        ("shows on oct 12", 0, (date(2026, 10, 12), date(2026, 10, 12))),
        ("a room for 3 nights", 1, (date(2026, 10, 1), date(2026, 10, 4))),
        ("a stay for 3 nights from oct 5", 1, (date(2026, 10, 5), date(2026, 10, 8))),
    ],
)
def test_ranges_resolve_as_documented(text: str, offset: int, expected: tuple[date, date]) -> None:
    assert parse_date_range(text, today=TODAY, start_offset_days=offset) == expected


def test_weekend_when_today_is_saturday_or_sunday() -> None:
    assert parse_date_range("this weekend", today=date(2026, 10, 3)) == (date(2026, 10, 3), date(2026, 10, 4))
    assert parse_date_range("this weekend", today=date(2026, 10, 4)) == (date(2026, 10, 4), date(2026, 10, 4))


def test_next_weekend_from_a_saturday_and_a_sunday() -> None:
    assert parse_date_range("next weekend", today=date(2026, 10, 3)) == (date(2026, 10, 10), date(2026, 10, 11))
    assert parse_date_range("next weekend", today=date(2026, 10, 4)) == (date(2026, 10, 10), date(2026, 10, 11))


def test_no_recognisable_phrase_returns_none() -> None:
    assert parse_date_range("what time is checkout", today=TODAY) is None
    assert parse_date_range("", today=TODAY) is None


@pytest.mark.parametrize(
    "text,nights",
    [
        ("book stay for next 5 days", 5),
        ("a room for 3 nights", 3),
        ("book a 4-night stay", 4),
        ("I need it for 2 nights from oct 5", 2),
        ("a room for a week", 7),
        ("for 6 days", 6),
    ],
)
def test_stay_length(text: str, nights: int) -> None:
    assert parse_stay_length(text) == nights


def test_stay_length_none_when_no_length_is_stated() -> None:
    assert parse_stay_length("book a room") is None
    assert parse_stay_length("please stay safe") is None


@given(st.text(max_size=80))
def test_never_raises_and_never_returns_end_before_start(text: str) -> None:
    result = parse_date_range(text, today=TODAY)
    if result is not None:
        start, end = result
        assert end >= start
    length = parse_stay_length(text)
    assert length is None or length >= 1
