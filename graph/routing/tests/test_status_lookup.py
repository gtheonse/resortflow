"""A status question about an existing booking is a lookup, not a new booking.

Found live from the UI: "check my stay status" answered "You didn't give an arrival date, so I used tomorrow ... please
approve: stay_booking". The possessive-lookup rule only fired when the message STARTED with a question word, and there was
no lookup cue for "status", so the bare service word fell through to the "assumed to be a new booking" fallback — for dining
that confirms a table without an approval step."""

from __future__ import annotations

from graph.models import Domain, RequestKind
from graph.routing.kinds import classify_kind
from graph.routing.matcher import match_services
from graph.routing.vocabulary import DEFAULT


def _decide(text: str):
    return classify_kind(text, match_services(text, DEFAULT), vocab=DEFAULT)


def test_status_questions_about_an_existing_booking_are_lookups() -> None:
    cases = {
        "check my stay status": [Domain.STAY],
        "check my dinner status": [Domain.DINING],
        "status of my room": [Domain.STAY],
        "my stay status": [Domain.STAY],
        "what is my stay status": [Domain.STAY],
        "check my spa appointment": [Domain.WELLNESS],
        "check on my reservation": [Domain.DINING],  # "reservation" is already a dining word (as in "my reservation")
        "show me my bookings": [],
        "check my booking status": [],
        "booking status": [],
    }
    for text, services in cases.items():
        decision = _decide(text)
        assert decision.kind == RequestKind.LOOKUP, (text, decision.kind)
        assert decision.services == services, (text, decision.services)


def test_things_that_only_look_similar_are_not_lookups() -> None:
    assert _decide("book a room for tomorrow").kind == RequestKind.BOOK
    assert _decide("a table for 4 tonight").kind == RequestKind.BOOK  # bare service stays a booking (feature 010)
    assert _decide("what time is check in").kind != RequestKind.LOOKUP
    assert _decide("what time is checkout").kind == RequestKind.FAQ
    assert _decide("check availability for dinner tomorrow").kind != RequestKind.LOOKUP
    assert _decide("what are the dining venues").kind == RequestKind.BROWSE
    assert _decide("list my bookings").kind == RequestKind.LOOKUP
