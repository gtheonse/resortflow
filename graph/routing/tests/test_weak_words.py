"""Feature 011 (found by the `near_miss_book` cases): `reserve`/`reserved`/`reserving` are generic booking verbs,
but they were plain dining words, so "reserve a spa treatment" also selected dining and would have started a
dining flow beside the spa one. A word marked `weak` in the vocabulary only counts when no other service is
matched strongly; feature 010's variant behaviour ("reserved" alone still means dining) is unchanged."""

from __future__ import annotations

import pytest

from graph.models import Domain
from graph.routing.matcher import match_services


@pytest.mark.parametrize(
    "text,services",
    [
        ("reserve a spa treatment tomorrow at 3pm", [Domain.WELLNESS]),
        ("reserve two tickets to the show", [Domain.ENTERTAINMENT]),
        ("I want to reserve a room for 3 nights", [Domain.STAY]),
        ("reserve a table for 4", [Domain.DINING]),
        ("reserve dinner and a show", [Domain.DINING, Domain.ENTERTAINMENT]),
    ],
)
def test_a_weak_verb_never_adds_dining_beside_a_strong_service(text: str, services: list[Domain]) -> None:
    assert sorted(match_services(text).services, key=lambda d: d.value) == sorted(services, key=lambda d: d.value)


@pytest.mark.parametrize("text", ["reserved", "reserving for 2 at 8pm", "reserve for tonight"])
def test_a_weak_word_alone_still_selects_its_service(text: str) -> None:
    assert match_services(text).services == [Domain.DINING]


def test_reservation_the_noun_stays_a_strong_dining_word() -> None:
    assert match_services("what time is my reservation").services == [Domain.DINING]
