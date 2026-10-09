"""Feature 010, T021 — date/time parsing for the booking lookup (research
R-10). `agents/dining/nodes/intent_parser.py::_extract_date`/`_extract_time`
only understand ISO dates and `tonight`/`today`/`tomorrow` plus `H(:MM)?
(am|pm)`/24h times — neither reads a month name ("Sep 30") at all, which is
exactly what spec.md's SC-010 lookup reproduction needs.

Deterministic pattern matching only (Constitution Principle I) — no model
call, no I/O."""

from __future__ import annotations

import re
from datetime import date, timedelta

_MONTHS: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.IGNORECASE
)
_DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\.?\b", re.IGNORECASE
)

_WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_WEEKDAY_ALT = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_WEEKDAY_RE = re.compile(rf"\b({_WEEKDAY_ALT})\b", re.IGNORECASE)

_TIME_12H_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)
_TIME_24H_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def parse_date(text: str, *, today: date) -> date | None:
    """The first recognisable date in `text`, resolved relative to
    `today`. A year-less date (month-day, day-month, or a bare weekday
    name) resolves to the next occurrence on or after `today` — never a
    date already in the past. Returns `None` when nothing matches."""
    lowered = text.lower()

    match = _ISO_DATE_RE.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    if "tonight" in lowered or "today" in lowered:
        return today
    if "tomorrow" in lowered:
        return today + timedelta(days=1)

    match = _MONTH_DAY_RE.search(text)
    if match:
        month = _MONTHS[match.group(1).lower()]
        day = int(match.group(2))
        return _next_occurrence(today, month, day)

    match = _DAY_MONTH_RE.search(text)
    if match:
        day = int(match.group(1))
        month = _MONTHS[match.group(2).lower()]
        return _next_occurrence(today, month, day)

    match = _WEEKDAY_RE.search(lowered)
    if match:
        target = _WEEKDAYS[match.group(1).lower()]
        days_ahead = (target - today.weekday()) % 7
        return today + timedelta(days=days_ahead)

    return None


def _next_occurrence(today: date, month: int, day: int) -> date | None:
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if candidate < today:
        try:
            return date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def parse_time(text: str) -> tuple[int, int] | None:
    """The first recognisable time in `text` as `(hour_24, minute)`. A
    bare hour with no am/pm and no colon (the whole trimmed `text` is just
    digits, e.g. `"8"`) is genuinely ambiguous — this returns the AM
    reading; callers needing the ambiguity-tolerant comparison use
    `time_matches` instead. A bare hour embedded in a longer sentence
    (`"sep 30, 8 PM"`'s `"30"`) is deliberately NOT matched here, to avoid
    misreading a date's day-of-month as a time — only an explicit am/pm or
    `HH:MM` form is read out of a full sentence."""
    match = _TIME_12H_RE.search(text)
    if match:
        hour = int(match.group(1)) % 12
        minute = int(match.group(2) or 0)
        if match.group(3).lower() == "pm":
            hour += 12
        return (hour, minute)

    match = _TIME_24H_RE.search(text)
    if match:
        return (int(match.group(1)), int(match.group(2)))

    stripped = text.strip()
    if stripped.isdigit() and 1 <= int(stripped) <= 12:
        return (int(stripped) % 12, 0)

    return None


def time_matches(text: str, candidate: tuple[int, int]) -> bool:
    """True if `candidate` (`hour_24, minute`) is consistent with the time
    named in `text` — for a bare hour with no am/pm, either the AM or PM
    reading matches (FR "a bare hour matches 08:00 or 20:00")."""
    parsed = parse_time(text)
    if parsed is None:
        return False
    hour, minute = parsed
    stripped = text.strip()
    if stripped.isdigit():
        return candidate in {(hour, minute), ((hour + 12) % 24, minute)}
    return candidate == (hour, minute)


# --- Feature 011: ranges and stay length (`contracts/browse-router-api.md`) ----------------------------

_NEXT_N_DAYS_RE = re.compile(r"\bnext\s+(\d{1,3})\s+days?\b", re.IGNORECASE)
_FOR_N_DAYS_RE = re.compile(r"\bfor\s+(\d{1,3})\s+days?\b", re.IGNORECASE)
_N_NIGHTS_RE = re.compile(r"\b(\d{1,3})[\s-]+nights?\b", re.IGNORECASE)
_A_WEEK_RE = re.compile(r"\b(?:for\s+)?a\s+week\b", re.IGNORECASE)
_THIS_WEEKEND_RE = re.compile(r"\b(?:this|the|coming)\s+weekend\b", re.IGNORECASE)
_NEXT_WEEKEND_RE = re.compile(r"\bnext\s+weekend\b", re.IGNORECASE)
_NEXT_WEEK_RE = re.compile(r"\bnext\s+week\b", re.IGNORECASE)
_RANGE_CONNECTOR_RE = re.compile(r"^\s*(?:to|until|through|thru|and|-|–)\s*$", re.IGNORECASE)
_DAY_ONLY_TAIL_RE = re.compile(r"^\s*(?:to|until|through|thru|-|–)\s*(\d{1,2})\b(?!\s*(?::|am\b|pm\b))", re.IGNORECASE)


def _month_day_matches(text: str) -> list[re.Match[str]]:
    return list(_MONTH_DAY_RE.finditer(text))


def _safe_add(start: date, days: int) -> date | None:
    try:
        return start + timedelta(days=days)
    except OverflowError:
        return None


def _two_date_range(text: str, today: date) -> tuple[date, date] | None:
    matches = _month_day_matches(text)
    if len(matches) >= 2:
        first, second = matches[0], matches[1]
        if _RANGE_CONNECTOR_RE.match(text[first.end() : second.start()]):
            start = _next_occurrence(today, _MONTHS[first.group(1).lower()], int(first.group(2)))
            if start is None:
                return None
            end = _next_occurrence(start, _MONTHS[second.group(1).lower()], int(second.group(2)))
            if end is not None and end >= start:
                return (start, end)
        return None
    if len(matches) == 1:
        first = matches[0]
        tail = _DAY_ONLY_TAIL_RE.match(text[first.end() :])
        if tail:
            start = _next_occurrence(today, _MONTHS[first.group(1).lower()], int(first.group(2)))
            if start is None:
                return None
            try:
                end = date(start.year, start.month, int(tail.group(1)))
            except ValueError:
                return None
            if end >= start:
                return (start, end)
    return None


def parse_stay_length(text: str) -> int | None:
    """Nights implied by a length phrase: `next N days`, `for N days`, `N nights`/`N-night`, `a week` (7).
    `None` when no length is stated. Never returns less than 1."""
    for pattern in (_N_NIGHTS_RE, _NEXT_N_DAYS_RE, _FOR_N_DAYS_RE):
        match = pattern.search(text)
        if match:
            n = int(match.group(1))
            return n if n >= 1 else None
    if _A_WEEK_RE.search(text):
        return 7
    return None


def _weekend(today: date, *, following: bool) -> tuple[date, date]:
    """The weekend this week (Saturday-Sunday; on a Sunday only today remains) or the one after it."""
    saturday = today - timedelta(days=1) if today.weekday() == 6 else today + timedelta(days=(5 - today.weekday()) % 7)
    if following:
        saturday = saturday + timedelta(days=7)
        return (saturday, saturday + timedelta(days=1))
    if today.weekday() == 6:
        return (today, today)
    return (saturday, saturday + timedelta(days=1))


def parse_explicit_range(text: str, *, today: date) -> tuple[date, date] | None:
    """Only an explicit two-date range ("oct 5 to oct 8") as `(arrival, departure)`, else `None` — for callers
    (the stay intent parser) that must not treat "next 5 days" or a single date as a range. Never raises."""
    try:
        found = _two_date_range(text, today)
    except Exception:  # noqa: BLE001 - a parser must degrade to "no range", never fail a turn
        return None
    return found if found is not None and found[1] > found[0] else None


def parse_date_range(text: str, *, today: date, start_offset_days: int = 0) -> tuple[date, date] | None:
    """A `(start, end)` inclusive window from the guest's words, or `None`. `start_offset_days` is how far
    from `today` a phrase with no start of its own begins (events 0, stays 1 — research R-06). Never raises
    and never returns `end < start`. Night-based phrases return `(arrival, departure)`."""
    try:
        explicit = _two_date_range(text, today)
        if explicit is not None:
            return explicit

        single = parse_date(text, today=today)
        base = single if single is not None else _safe_add(today, start_offset_days)
        if base is None:
            return None

        nights = _N_NIGHTS_RE.search(text)
        if nights and int(nights.group(1)) >= 1:
            end = _safe_add(base, int(nights.group(1)))
            return (base, end) if end is not None else None
        if _A_WEEK_RE.search(text):
            end = _safe_add(base, 7)
            return (base, end) if end is not None else None
        for pattern in (_NEXT_N_DAYS_RE, _FOR_N_DAYS_RE):
            match = pattern.search(text)
            if match and int(match.group(1)) >= 1:
                end = _safe_add(base, int(match.group(1)) - 1)
                return (base, end) if end is not None else None

        if _NEXT_WEEKEND_RE.search(text):
            return _weekend(today, following=True)
        if _THIS_WEEKEND_RE.search(text):
            return _weekend(today, following=False)
        if _NEXT_WEEK_RE.search(text):
            monday = today + timedelta(days=7 - today.weekday())
            return (monday, monday + timedelta(days=6))

        if single is not None:
            return (single, single)
        return None
    except (OverflowError, ValueError):
        return None
