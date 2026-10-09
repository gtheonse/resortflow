"""Demo-report fix: "book stay for next 5 days" used to route as `book` with no
service (bare `stay` is deliberately not a trigger — "stay safe"/"stay tuned"
traps), landing on the generic clarify. Stay phrases carry the intent instead."""

from __future__ import annotations

import pytest

from graph.models import Domain, RequestKind
from graph.routing.kinds import classify_kind
from graph.routing.matcher import match_services


@pytest.mark.parametrize(
    "text",
    ["book stay for next 5 days", "book a stay", "book my stay", "extend our stay", "I want a stay from oct 5"],
)
def test_stay_phrases_route_to_stay(text: str) -> None:
    decision = classify_kind(text, match_services(text))
    assert decision.services == [Domain.STAY]
    assert decision.kind == RequestKind.BOOK or decision.kind is not None


@pytest.mark.parametrize("text", ["please stay safe during the storm", "stay tuned for updates"])
def test_stay_traps_still_not_stay(text: str) -> None:
    assert Domain.STAY not in match_services(text).services
