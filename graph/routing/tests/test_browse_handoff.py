"""Feature 011, SC-006 — a booking request right after a browse inherits that browse's service.

Found live: after a dining list, "book Sakura Sushi Bar for 2 tomorrow at 7pm" got the generic clarifying question,
because a venue name is not a service word. One turn only, like every other carry-over."""

from __future__ import annotations

from datetime import date

from graph.models import Domain, RequestKind
from graph.routing.kinds import PreviousTurn, classify_kind
from graph.routing.matcher import match_services
from graph.routing.vocabulary import DEFAULT

TODAY = date(2026, 9, 30)


def _classify(text: str, previous: PreviousTurn | None):
    return classify_kind(text, match_services(text, DEFAULT), previous=previous, vocab=DEFAULT, today=TODAY)


def _after_browse(*services: Domain) -> PreviousTurn:
    return PreviousTurn(kind=RequestKind.BROWSE, services=list(services))


def test_a_booking_naming_a_venue_right_after_a_dining_browse_is_a_dining_booking() -> None:
    decision = _classify("book Sakura Sushi Bar for 2 tomorrow at 7pm", _after_browse(Domain.DINING))
    assert decision.kind == RequestKind.BOOK
    assert decision.services == [Domain.DINING]
    assert decision.carried_over is True


def test_a_booking_naming_an_event_right_after_an_events_browse_is_an_entertainment_booking() -> None:
    decision = _classify("get me two for the Golden Tour", _after_browse(Domain.ENTERTAINMENT))
    assert decision.kind == RequestKind.BOOK and decision.services == [Domain.ENTERTAINMENT]


def test_the_message_own_service_wins_over_the_previous_browse() -> None:
    decision = _classify("book a spa treatment tomorrow", _after_browse(Domain.DINING))
    assert decision.kind == RequestKind.BOOK
    assert decision.services == [Domain.WELLNESS] and decision.carried_over is False


def test_an_overview_or_gaming_browse_hands_nothing_off() -> None:
    for previous in (_after_browse(), _after_browse(Domain.GAMING)):
        decision = _classify("book Sakura Sushi Bar for 2", previous)
        assert decision.carried_over is False
        assert not (decision.kind == RequestKind.BOOK and decision.services)


def test_with_no_previous_browse_a_venue_name_alone_is_not_inferred_as_dining() -> None:
    decision = _classify("book Sakura Sushi Bar for 2 tomorrow at 7pm", None)
    assert not (decision.kind == RequestKind.BOOK and decision.services == [Domain.DINING])


def test_narrowing_still_wins_over_handoff() -> None:
    decision = _classify("only japanese", _after_browse(Domain.DINING))
    assert decision.kind == RequestKind.BROWSE and decision.carried_over is True


def test_a_mixed_services_browse_hands_off_only_bookable_ones() -> None:
    decision = _classify("book Sakura Sushi Bar for 2", _after_browse(Domain.DINING, Domain.ENTERTAINMENT))
    assert decision.kind == RequestKind.BOOK
    assert set(decision.services) == {Domain.DINING, Domain.ENTERTAINMENT}
