"""Feature 010, T020 — `graph.routing.temporal` (research R-10): date/time
parsing for the booking lookup. `agents/dining/nodes/intent_parser.py`'s
`_extract_date`/`_extract_time` cannot read "Sep 30" at all — the exact gap
behind spec.md's own SC-010 reproduction. Must fail before T021."""

from __future__ import annotations

from datetime import date

from graph.routing.temporal import parse_date, parse_time

_TODAY = date(2026, 9, 27)


def test_month_name_date_forms() -> None:
    assert parse_date("sep 30", today=_TODAY) == date(2026, 9, 30)
    assert parse_date("september 30th", today=_TODAY) == date(2026, 9, 30)
    assert parse_date("30 sep", today=_TODAY) == date(2026, 9, 30)


def test_iso_date() -> None:
    assert parse_date("2026-09-30", today=_TODAY) == date(2026, 9, 30)


def test_relative_dates() -> None:
    assert parse_date("tonight", today=_TODAY) == _TODAY
    assert parse_date("today", today=_TODAY) == _TODAY
    assert parse_date("tomorrow", today=_TODAY) == date(2026, 9, 28)


def test_weekday_name_resolves_to_next_occurrence() -> None:
    # 2026-09-27 is a Sunday; "wednesday" is the 30th.
    assert parse_date("wednesday", today=_TODAY) == date(2026, 9, 30)


def test_year_less_date_resolves_forward_from_today() -> None:
    # "jan 15" from 2026-09-27 means 2027-01-15, not the past.
    assert parse_date("jan 15", today=_TODAY) == date(2027, 1, 15)


def test_no_date_found_returns_none() -> None:
    assert parse_date("what have I booked", today=_TODAY) is None


def test_time_forms() -> None:
    assert parse_time("8") in ((8, 0), (20, 0))  # bare hour: AM or PM ambiguous
    assert parse_time("8pm") == (20, 0)
    assert parse_time("8 PM") == (20, 0)
    assert parse_time("20:00") == (20, 0)
    assert parse_time("8:30 pm") == (20, 30)


def test_bare_hour_matches_am_or_pm() -> None:
    from graph.routing.temporal import time_matches

    assert time_matches("8", (8, 0))
    assert time_matches("8", (20, 0))
    assert not time_matches("8", (8, 30))


def test_no_time_found_returns_none() -> None:
    assert parse_time("what have I booked") is None
