"""Feature 010 — deterministic reply templates.

Every reply in this module is a plain string template, never a model call
(Constitution Principle I/IV). See
`specs/010-tiered-intent-routing/contracts/turn-replies.md` for the exact
wording rules each function here implements.
"""

from __future__ import annotations

from datetime import date, datetime

from graph.bookings import BookingRecord
from graph.routing.matcher import tokenize

_SUPPORTED_SERVICES_LINE = (
    "I can help with dining, shows and entertainment, spa and wellness, your room and stay, "
    "and loyalty questions."
)

_GREETING_WORDS = frozenset({"hi", "hello", "hey"})
"""`contracts/turn-replies.md`'s two smalltalk categories (greeting vs.
thanks/acknowledgement) are picked by word content, not exact-string
match — found live (demo report): "hi there" fell through to the
acknowledgement reply ("You're welcome...") because the original check
required the whole trimmed message to equal "hi"/"hello"/"hey" exactly."""


def decline_reply(text: str) -> str:
    """FR-018: states what the assistant cannot do and lists the services
    it can help with — never any knowledge-base text (the node calling
    this has no retriever dependency at all, so there is structurally
    nothing to include)."""
    lowered = text.lower()
    if "flight" in lowered or "airline" in lowered:
        subject = "book flights"
    elif "weather" in lowered:
        subject = "help with the weather"
    elif "train" in lowered:
        subject = "book train travel"
    elif "car" in lowered or "rental" in lowered or "taxi" in lowered or "uber" in lowered:
        subject = "arrange a rental car or ride"
    elif "passport" in lowered or "visa" in lowered:
        subject = "help with passports or visas"
    elif "tax" in lowered:
        subject = "help with taxes"
    else:
        subject = "help with that"
    return f"I can't {subject} — I'm the resort assistant. {_SUPPORTED_SERVICES_LINE}"


def modify_reply(matched: list[BookingRecord]) -> str:
    """FR-017: acknowledges a change/cancel request and identifies the
    booking where determinable — the hand-off itself is the NEXT message,
    appended by `HUMAN_HANDOFF` (`contracts/turn-replies.md`)."""
    if not matched:
        return "I can't change or cancel bookings myself, so I've passed your request to a team member."
    names = ", ".join(_venue_and_when(r) for r in matched)
    return (
        f"I can't change or cancel bookings myself, so I've passed your request about "
        f"{names} to a team member."
    )


def smalltalk_reply(text: str) -> str:
    """FR-019: a brief reply, never retrieval, never the model."""
    if any(token in _GREETING_WORDS for token in tokenize(text)):
        return f"Hello! {_SUPPORTED_SERVICES_LINE} What would you like?"
    return "You're welcome — anything else I can help with?"


def _format_clock(dt: datetime) -> str:
    """Cross-platform 12-hour clock formatting — deliberately not
    `strftime("%-I:%M %p")`, whose `%-I` is a glibc/macOS extension not
    available on Windows (this project's own dev environment)."""
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{hour12}:{dt.minute:02d} {ampm}"


def _format_when(dt: datetime) -> str:
    return f"{dt.strftime('%b')} {dt.day} at {_format_clock(dt)}"


def _party_suffix(record: BookingRecord) -> str:
    return f", party of {record.party_size}" if record.party_size else ""


def _venue_and_when(record: BookingRecord) -> str:
    when = f" — {_format_when(record.starts_at)}" if record.starts_at is not None else ""
    return f"{record.venue}{when}{_party_suffix(record)}"


def format_turn_outcome_line(record: BookingRecord) -> str:
    """One line for a booking record that changed **this turn**
    (`contracts/turn-replies.md`'s booking-reply table). Never applies
    "confirmed"/"booked"/"all set" to anything but a `record.status ==
    "confirmed"` item (FR-032, SC-011)."""
    base = _venue_and_when(record)
    if record.status == "confirmed":
        code = f" (confirmation {record.confirmation_code})" if record.confirmation_code else ""
        return f"Confirmed: {base}{code}."
    if record.status in ("held", "pending"):
        if record.expires_at is not None:
            return f"{base} is held, not yet confirmed; the hold expires at {_format_clock(record.expires_at)}."
        return f"{base} is held, not yet confirmed; the hold's status could not be confirmed right now."
    # released / expired / orphan_released
    return f"{base} is no longer held."


def format_lookup_match_line(record: BookingRecord, recheck: str | None) -> str:
    """One line for a matched lookup result (FR-014/FR-015,
    `contracts/turn-replies.md`). `recheck` is `None` when the record is
    not `held`/`pending` (nothing to re-check)."""
    base = _venue_and_when(record)
    if record.status == "confirmed":
        code = f" (confirmation {record.confirmation_code})" if record.confirmation_code else ""
        return f"{base} — confirmed{code}."
    if record.status in ("held", "pending"):
        if recheck == "active":
            expiry = f" the hold expires at {_format_clock(record.expires_at)}." if record.expires_at else ""
            return f"{base} is held, not yet confirmed;{expiry}"
        if recheck == "not_active":
            return f"{base} — the hold has lapsed."
        return f"{base} — held, status could not be confirmed right now."
    return f"{base} is no longer held."


def format_current_status_line(record: BookingRecord) -> str:
    """One line describing a booking's **current** status, for the
    "here's where things stand" fallback used when no `turn_baseline` is
    available (a pre-010 conversation's first post-upgrade turn) — a plain
    factual statement, never framed as something that happened this turn
    (FR-031)."""
    base = _venue_and_when(record)
    if record.status == "confirmed":
        code = f" (confirmation {record.confirmation_code})" if record.confirmation_code else ""
        return f"{base} — confirmed{code}."
    if record.status in ("held", "pending"):
        if record.expires_at is not None:
            return f"{base} — held, not yet confirmed, expiring at {_format_clock(record.expires_at)}."
        return f"{base} — held, not yet confirmed."
    return f"{base} — no longer held."


# --- Feature 011: browse reply templates (contracts/browse-replies.md) -----------------------------------
import re as _re

BROWSE_MAX_ITEMS = 10

DIETARY_BROWSE_NOTE = (
    "You state your dietary restriction when you book, where it is recorded and passed to the kitchen — "
    "I can't say a venue is safe for it from a list."
)
"""FR-009: a code constant. Avoids the words "confirmed"/"booked" so `browse_reply_violations` stays exact."""

BROWSE_DINING_NEXT_STEP = "Want me to book one? Tell me the venue, party size, date and time."


def browse_more_line(total: int, shown: int, hint: str = "say a filter to narrow") -> str:
    return f"…and {total - shown} more — {hint}." if total > shown else ""


def browse_provider_unavailable(things: str) -> str:
    return f"I couldn't retrieve the {things} list right now — please try again in a moment."


def browse_empty_reply(things: str, qualifier: str, widen_hint: str) -> str:
    qualifier = f" {qualifier}" if qualifier else ""
    return f"I couldn't find any {things}{qualifier}. {widen_hint}"


def browse_dining_reply(property_name: str, venues: list[tuple[str, str, str]], *, dietary_note: bool) -> str:
    """`venues` = (name, cuisine, price_band), already filtered. Caller guarantees non-empty."""
    shown = venues[:BROWSE_MAX_ITEMS]
    lines = [f"Here are the restaurants at {property_name}:"]
    if dietary_note:
        lines.insert(0, DIETARY_BROWSE_NOTE)
    lines += [f"• {name} — {cuisine.replace('_', ' ')}, {price}" for name, cuisine, price in shown]
    more = browse_more_line(len(venues), len(shown), "say a cuisine or price to narrow")
    if more:
        lines.append(more)
    lines.append(BROWSE_DINING_NEXT_STEP)
    return "\n".join(lines)


_FORBIDDEN_WORDS = _re.compile(r"\b(confirmed|booked|held|all set)\b", _re.IGNORECASE)
_SAFETY_CLAIM = _re.compile(r"\b(?:is|are|be)\s+(?:completely\s+|totally\s+)?(?:safe|suitable)\b", _re.IGNORECASE)


def browse_reply_violations(reply: str) -> list[str]:
    """Forbidden-content check (contracts/browse-replies.md): nothing was booked, and no venue is described as
    safe or suitable for an allergy. Returns the violated rule names (empty = clean)."""
    found: list[str] = []
    if _FORBIDDEN_WORDS.search(reply):
        found.append("booking-claim")
    if _SAFETY_CLAIM.search(reply.replace(DIETARY_BROWSE_NOTE, "")):
        found.append("safety-claim")
    return found


def browse_overview_reply(property_name: str, *, casino: bool) -> str:
    """Overview (no service named; never a decline). `casino` is decided by a fresh gaming-clearance check —
    restricted, self-excluded or unavailable guests get the loyalty-only line (FR-015a, fail closed)."""
    gaming_line = (
        '• Casino games and loyalty — try "what casino games do you have" or "what\'s my loyalty tier".'
        if casino
        else '• Loyalty — try "what\'s my loyalty tier".'
    )
    return "\n".join(
        [
            "Here's what I can arrange:",
            f'• Dining — restaurants at {property_name}. Try "what restaurants do you have".',
            '• Shows and events — try "what\'s on this weekend".',
            '• Spa and wellness — try "what spa treatments do you have".',
            '• A room — try "what rooms are available Oct 5 to Oct 8".',
            gaming_line,
            "What would you like to look at?",
        ]
    )


def browse_date(d: date) -> str:
    return f"{d:%b} {d.day}"


def browse_events_window_phrase(start: date, end: date, source: str) -> str:
    phrase = f"from {browse_date(start)} to {browse_date(end)}"
    if source == "default":
        return f"{phrase} (I used the next 7 days)"
    if source == "stay_period":
        return f"{phrase} (your stay: {browse_date(start)}–{browse_date(end)})"
    return phrase


BROWSE_EVENTS_NEXT_STEP = "Want tickets? Tell me the event and how many."
BROWSE_NEEDS_STAY_DATES = "I don't see a stay on your booking yet — which dates should I look at?"


def browse_events_reply(window_phrase: str, rows: list[str], total: int) -> str:
    """`rows` are pre-formatted item lines (already capped by the caller); `total` is the count before the cap."""
    lines = [f"Events {window_phrase}:"] + [f"• {row}" for row in rows]
    more = browse_more_line(total, len(rows), "give me a date or type of show to narrow")
    if more:
        lines.append(more)
    lines.append(BROWSE_EVENTS_NEXT_STEP)
    return "\n".join(lines)


BROWSE_ROOMS_NEEDS_DATES = "Which dates? Give me an arrival and departure, or how many nights."
BROWSE_ROOMS_NEXT_STEP = "Want me to hold one? Tell me which."


def browse_rooms_reply(arrival: date, departure: date, nights: int, adults: int, assumed_adults: bool, rows: list[str], total: int) -> str:
    assumed = " (I assumed 2 adults)" if assumed_adults else ""
    unit = "night" if nights == 1 else "nights"
    lines = [
        f"Rooms available {browse_date(arrival)} to {browse_date(departure)} ({nights} {unit}, {adults} adults){assumed}:"
    ] + [f"• {row}" for row in rows]
    more = browse_more_line(total, len(rows), "give me a room type to narrow")
    if more:
        lines.append(more)
    lines.append(BROWSE_ROOMS_NEXT_STEP)
    return "\n".join(lines)


BROWSE_WELLNESS_NEXT_STEP = "Want to book one? Tell me the service, date and time."


def browse_wellness_reply(rows: list[str], total: int) -> str:
    lines = ["Spa and wellness services:"] + [f"• {row}" for row in rows]
    more = browse_more_line(total, len(rows), "give me a category to narrow")
    if more:
        lines.append(more)
    lines.append(BROWSE_WELLNESS_NEXT_STEP)
    return "\n".join(lines)


def stay_assumed_arrival_note(arrival: date, departure: date, nights: int) -> str:
    """Feature 011 (FR-018, US4/AC1): a stay request with no arrival date uses tomorrow, and says so — the stay
    flow has no ask-for-arrival step by design, so the assumption is disclosed instead of silent."""
    unit = "night" if nights == 1 else "nights"
    return (
        f"You didn't give an arrival date, so I used tomorrow, {browse_date(arrival)}, for {nights} {unit} "
        f"(departing {browse_date(departure)}). If that isn't right, decline and tell me the arrival date."
    )
