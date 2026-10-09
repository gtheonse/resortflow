"""Feature 011, T024 — the browse reply composer behind the `BROWSE` node
(`specs/011-browse-requests/contracts/graph-topology-v011.md`).

This module is deliberately separate from `graph/supervisor.py` so its guarantees are checkable by inspection
(`agents/tests/test_browse_readonly_structural.py`): it calls only **read** tool methods, never mutates, and never
reads knowledge-base text. The node wrapper in `supervisor.py` adds the policy post-flight / offer-agent calls that
every END-bound node applies.

Per-service handlers are filled in story by story (US1 dining, US2 overview, US3 events, US4 rooms, US5 wellness);
until a story lands its handler is a plain "not available yet" line — never a crash and never knowledge-base text."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from graph import replies
from graph.bookings import BookingRecord, stay_period
from graph.models import BrowseFilters, DateWindow, Domain, RoutingDecision
from graph.routing.browse import PROPERTY_NAMES, detect_other_property
from policy.gaming import require_gaming_clear

logger = logging.getLogger(__name__)

MAX_ITEMS = 10
"""FR-016: lists are bounded; the reply says how many more there are."""

DEFAULT_EVENT_WINDOW_DAYS = 7
"""Clarified 2026-09-30: with no dates, events default to the next 7 days, and the reply says so."""

_SERVICE_ORDER: tuple[Domain, ...] = (Domain.DINING, Domain.ENTERTAINMENT, Domain.WELLNESS, Domain.STAY)


@dataclass(frozen=True)
class BrowseContext:
    """Everything a handler needs. `tenant_id`/`guest_id` come from verified state, never message text (FR-007)."""

    routing: RoutingDecision
    tenant_id: str
    guest_id: str
    text: str
    records: list[BookingRecord]
    tools_factory: dict[Domain, Callable[..., Any]]
    today: date
    window: DateWindow | None = None
    """The effective window after stay-period resolution (`None` = none stated; a handler applies its own default)."""
    needs_dates: bool = False
    """"my stay" was asked with no stay on record: the reply asks for dates and reads nothing (FR-019)."""
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def property_name(self) -> str:
        return PROPERTY_NAMES.get(self.tenant_id, "the resort")

    def tools_for(self, domain: Domain) -> Any | None:
        factory = self.tools_factory.get(domain)
        return factory(self.tenant_id, self.guest_id) if factory is not None else None


Handler = Callable[[BrowseContext], Awaitable[str]]


def effective_window(routing: RoutingDecision, records: list[BookingRecord]) -> tuple[DateWindow | None, bool]:
    """(window, needs_dates). A `needs_dates` marker is upgraded to the guest's own stay dates when a held or
    confirmed room is on record (research R-04); with none, the caller asks for dates instead of assuming any."""
    window = routing.window
    if window is not None and window.source == "needs_dates":
        period = stay_period(records)
        if period is not None:
            return DateWindow(start=period[0], end=period[1], source="stay_period"), False
        return None, True
    return window, False


def _price_matches(band: str, wanted: str | None) -> bool:
    if not wanted:
        return True
    if wanted.startswith("<="):
        return len(band) <= len(wanted) - 2
    if wanted.startswith(">="):
        return len(band) >= len(wanted) - 2
    return band == wanted


async def _dining_handler(ctx: BrowseContext) -> str:
    """US1: read `list_venues`, narrow locally by the structured filters, format with templates only."""
    filters = ctx.routing.browse_filters or BrowseFilters()
    tools = ctx.tools_for(Domain.DINING)
    if tools is None:
        raise RuntimeError("no dining tools")
    response = await tools.list_venues(cuisine=filters.cuisine)
    venues = [
        v for v in response.venues
        if v.tenant_id == ctx.tenant_id
        and (not filters.cuisine or v.cuisine == filters.cuisine)
        and _price_matches(v.price_band, filters.price_band)
        and (filters.dietary is None or filters.dietary.value in v.supported_dietary)
    ]
    if not venues:
        parts = [p for p in (filters.cuisine, filters.price_band, filters.dietary.value if filters.dietary else None) if p]
        qualifier = f"matching {', '.join(parts)}" if parts else ""
        return replies.browse_empty_reply(
            "restaurants", qualifier, "Try a different cuisine or price range, or ask for all the restaurants."
        )
    rows = [(v.name, v.cuisine, v.price_band) for v in venues]
    return replies.browse_dining_reply(ctx.property_name, rows, dietary_note=filters.dietary is not None)


_TENANT_TZ = {"MGV": "America/Los_Angeles", "MHB": "America/New_York"}
_UNBOOKABLE = frozenset({"sold_out", "cancelled", "canceled"})


def _local_start(tenant_id: str, start_utc: str) -> datetime:
    moment = datetime.fromisoformat(start_utc)
    try:
        return moment.astimezone(ZoneInfo(_TENANT_TZ.get(tenant_id, "UTC")))
    except Exception:  # noqa: BLE001 - tzdata missing: fall back to UTC rather than fail the list
        return moment


def _event_row(local: datetime, event: Any) -> str:
    price = (event.price_range or {}).get("min")
    price_part = f", from ${price:g}" if isinstance(price, (int, float)) else ""
    clock = f"{local:%I:%M %p}".lstrip("0")
    return (
        f"{event.name} — {event.classification}, {local:%a} {replies.browse_date(local.date())} at {clock}"
        + price_part
    )


async def _events_handler(ctx: BrowseContext) -> str:
    """US3: one `search_events()` call, then window/status filtering, sorting and the cap are local."""
    if ctx.needs_dates:
        return replies.BROWSE_NEEDS_STAY_DATES
    window = ctx.window or DateWindow(
        start=ctx.today, end=ctx.today + timedelta(days=DEFAULT_EVENT_WINDOW_DAYS - 1), source="default"
    )
    tools = ctx.tools_for(Domain.ENTERTAINMENT)
    if tools is None:
        raise RuntimeError("no entertainment tools")
    category = (ctx.routing.browse_filters or BrowseFilters()).category
    response = await tools.search_events(classification=category)
    dated = sorted(
        (
            (_local_start(ctx.tenant_id, e.start_utc), e)
            for e in response.events
            if e.status not in _UNBOOKABLE and e.venue.get("tenant_id", ctx.tenant_id) == ctx.tenant_id
            and (not category or e.classification == category)
        ),
        key=lambda pair: pair[0],
    )
    in_window = [(t, e) for t, e in dated if window.start <= t.date() <= window.end]
    phrase = replies.browse_events_window_phrase(window.start, window.end, window.source)
    if not in_window:
        later = next(((t, e) for t, e in dated if t.date() > window.end), None)
        hint = (
            f"The next one is {_event_row(*later)}. Want a different date range?"
            if later
            else "Try a different date range."
        )
        return replies.browse_empty_reply("events", phrase, hint)
    rows = [_event_row(t, e) for t, e in in_window[:MAX_ITEMS]]
    return replies.browse_events_reply(phrase, rows, len(in_window))


async def _rooms_handler(ctx: BrowseContext) -> str:
    """US4: `search_rooms(arrival, departure, adults)` for the stated/stay window; rooms with no units left are
    omitted. With no dates it asks for them and reads nothing (FR-019)."""
    window = ctx.window
    if window is None or ctx.needs_dates:
        return replies.BROWSE_ROOMS_NEEDS_DATES
    departure = window.end if window.end > window.start else window.start + timedelta(days=1)
    party = (ctx.routing.browse_filters or BrowseFilters()).party_size
    adults = party or 2
    tools = ctx.tools_for(Domain.STAY)
    if tools is None:
        raise RuntimeError("no stay tools")
    offers = [
        o
        for o in await tools.search_rooms(arrival=window.start.isoformat(), departure=departure.isoformat(), adults=adults)
        if o.AVAILABLE_UNITS > 0 and o.NIGHTLY_RATES
    ]
    nights = (departure - window.start).days
    if not offers:
        return replies.browse_empty_reply(
            "rooms", f"for {replies.browse_date(window.start)} to {replies.browse_date(departure)}",
            "Try different dates or a smaller party.",
        )
    offers.sort(key=lambda o: min(r.AMOUNT for r in o.NIGHTLY_RATES))
    rows = [f"{o.ROOM_DESCRIPTION} — from ${min(r.AMOUNT for r in o.NIGHTLY_RATES):g}/night" for o in offers[:MAX_ITEMS]]
    return replies.browse_rooms_reply(window.start, departure, nights, adults, party is None, rows, len(offers))


async def _wellness_handler(ctx: BrowseContext) -> str:
    """US5: `list_services(category)`; the list is the provider's, formatted by template only."""
    category = (ctx.routing.browse_filters or BrowseFilters()).category
    tools = ctx.tools_for(Domain.WELLNESS)
    if tools is None:
        raise RuntimeError("no wellness tools")
    response = await tools.list_services(category=category)
    services = [s for s in response.services if not category or s.category == category]
    if not services:
        return replies.browse_empty_reply(
            "spa and wellness services", f"in {category}" if category else "",
            "Try a different category, or ask for all the spa and wellness services.",
        )
    rows = [f"{s.name} — {s.category}, {s.duration_minutes} min, ${s.price:g}" for s in services[:MAX_ITEMS]]
    return replies.browse_wellness_reply(rows, len(services))


def _stub(label: str) -> Handler:
    async def handler(ctx: BrowseContext) -> str:
        return f"I can't list the {label} just yet."

    return handler


HANDLERS: dict[Domain, Handler] = {
    Domain.DINING: _dining_handler,
    Domain.ENTERTAINMENT: _events_handler,
    Domain.WELLNESS: _wellness_handler,
    Domain.STAY: _rooms_handler,
}
async def _overview_handler(ctx: BrowseContext) -> str:
    """US2: the fixed overview. The casino line is shown only when `require_gaming_clear` passes, called fresh on
    this turn and failing closed on any error (Principle IV; never cached across turns)."""
    casino = False
    tools = ctx.tools_for(Domain.GAMING)
    if tools is not None:
        try:
            casino = (await require_gaming_clear(tools, guest_id=ctx.guest_id)).permitted
        except Exception:  # noqa: BLE001 - fail closed
            casino = False
    return replies.browse_overview_reply(ctx.property_name, casino=casino)


OVERVIEW_HANDLER: Handler = _overview_handler
"""Both replaced by the story tasks (T029, T033, T036, T040, T043) — the registry is the extension point."""


_UNAVAILABLE_LABEL: dict[Domain, str] = {
    Domain.DINING: "restaurant",
    Domain.ENTERTAINMENT: "events",
    Domain.WELLNESS: "spa and wellness services",
    Domain.STAY: "rooms",
}


async def _section(domain: Domain, ctx: BrowseContext) -> str:
    try:
        return await HANDLERS[domain](ctx)
    except Exception:
        logger.warning("browse section for %s failed", domain.value, exc_info=True)
        return f"I couldn't retrieve the {_UNAVAILABLE_LABEL[domain]} list right now — please try again in a moment."


async def compose_browse_reply(ctx: BrowseContext) -> str:
    """One section per requested service in a fixed order, or the overview when none was named. A service whose
    handler raises is reported as unavailable for that section only (FR-017), never as invented items."""
    services = [d for d in _SERVICE_ORDER if d in ctx.routing.services]
    if not services:
        sections = [await OVERVIEW_HANDLER(ctx)]
    else:
        sections = [await _section(domain, ctx) for domain in services]
    reply = "\n\n".join(section for section in sections if section)

    other = detect_other_property(ctx.text, ctx.tenant_id)
    if other is not None:
        reply = f"I can show {ctx.property_name}'s options only. {reply}"
    return reply
