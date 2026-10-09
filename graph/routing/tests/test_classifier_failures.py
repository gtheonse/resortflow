"""Feature 010, T033 — the tier-2 failure matrix
(`contracts/router-api.md`) plus the two extra rows analyze found: L3
(tenant/guest leakage rejected) and the spy-classifier / graph-level
SC-006 cases. Uses a fake `RoutingClassifier`, never a live model.

Must fail before T040 (`graph.routing.classifier`)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from graph.models import Domain, RequestKind, Tier2Outcome
from graph.routing.classifier import ClassifierOutput, ClassifierResult
from graph.routing.router import route


class _FakeClassifier:
    """A `RoutingClassifier` whose behaviour is set per test — never a
    real model call."""

    def __init__(self, result: ClassifierResult, *, calls: list[str] | None = None) -> None:
        self._result = result
        self._calls = calls if calls is not None else []

    async def classify(self, text: str, *, previous: Any = None) -> ClassifierResult:
        self._calls.append(text)
        return self._result


def _low_confidence_text() -> str:
    """A message tiers 0/1 cannot decide at all — no service, no kind
    signal, not short enough for smalltalk."""
    return "flooble the wobblesnort arrangement please"


@pytest.mark.parametrize(
    "result,expected_outcome",
    [
        (ClassifierResult(output=None, outcome=Tier2Outcome.UNAVAILABLE), Tier2Outcome.UNAVAILABLE),
        (ClassifierResult(output=None, outcome=Tier2Outcome.TIMEOUT), Tier2Outcome.TIMEOUT),
        (ClassifierResult(output=None, outcome=Tier2Outcome.INVALID), Tier2Outcome.INVALID),
    ],
)
def test_every_failure_outcome_routes_to_undecided(result: ClassifierResult, expected_outcome: Tier2Outcome) -> None:
    decision = asyncio.run(route(_low_confidence_text(), previous=None, classifier=_FakeClassifier(result)))
    assert decision.kind is None
    assert decision.tier2_outcome == expected_outcome


def test_unknown_service_or_kind_outside_enum_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ClassifierOutput(kind="teleport", services=[])  # type: ignore[arg-type]


def test_more_than_five_services_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ClassifierOutput(kind=RequestKind.BOOK, services=[Domain.DINING] * 6)


def test_tenant_or_guest_key_in_output_is_rejected() -> None:
    """analyze L3: tenant isolation is a mandatory test category — a
    classifier output smuggling a tenant/guest identity must never be
    accepted (FR-012)."""
    with pytest.raises(ValidationError):
        ClassifierOutput.model_validate({"kind": "book", "services": [], "tenant_id": "MGV"})
    with pytest.raises(ValidationError):
        ClassifierOutput.model_validate({"kind": "book", "services": [], "guest_id": "guest-1"})


def test_a_valid_but_injected_output_still_follows_its_bounded_route() -> None:
    """A prompt-injected-but-schema-valid `book` output is accepted (the
    bounded-damage argument, ADR-055) — the classifier's job is only to
    keep the OUTPUT inside the allowed set, not to detect injection."""
    output = ClassifierOutput(kind=RequestKind.BOOK, services=[Domain.DINING])
    result = ClassifierResult(output=output, outcome=Tier2Outcome.DECIDED)
    decision = asyncio.run(route(_low_confidence_text(), previous=None, classifier=_FakeClassifier(result)))
    assert decision.kind == RequestKind.BOOK
    assert decision.services == [Domain.DINING]
    assert decision.tier2_outcome == Tier2Outcome.DECIDED


# --- Spy: high-confidence / smalltalk / out_of_scope never call tier 2 -----


def test_high_confidence_message_never_calls_the_classifier() -> None:
    calls: list[str] = []
    fake = _FakeClassifier(ClassifierResult(output=None, outcome=Tier2Outcome.UNAVAILABLE), calls=calls)
    asyncio.run(route("book a table for 4 tonight", previous=None, classifier=fake))
    assert calls == []


def test_smalltalk_never_calls_the_classifier() -> None:
    calls: list[str] = []
    fake = _FakeClassifier(ClassifierResult(output=None, outcome=Tier2Outcome.UNAVAILABLE), calls=calls)
    asyncio.run(route("thanks", previous=None, classifier=fake))
    assert calls == []


def test_out_of_scope_never_calls_the_classifier() -> None:
    calls: list[str] = []
    fake = _FakeClassifier(ClassifierResult(output=None, outcome=Tier2Outcome.UNAVAILABLE), calls=calls)
    asyncio.run(route("book flight for me to fly to India", previous=None, classifier=fake))
    assert calls == []


def test_low_confidence_message_does_call_the_classifier() -> None:
    calls: list[str] = []
    fake = _FakeClassifier(ClassifierResult(output=None, outcome=Tier2Outcome.UNAVAILABLE), calls=calls)
    asyncio.run(route(_low_confidence_text(), previous=None, classifier=fake))
    assert calls == [_low_confidence_text()]


def test_router_level_turn_completes_undecided_with_classifier_unavailable() -> None:
    """SC-006 at the router level: with tier 2 unavailable, `route()`
    still returns an undecided decision rather than raising. The
    full-graph version of this (the turn actually reaching `CLARIFY`
    through the compiled supervisor graph) is
    `agents/tests/test_sc006_graph_level.py`, added once `INTENT_ROUTER`
    is wired (T043/T044)."""
    from graph.routing.classifier import UnavailableClassifier

    decision = asyncio.run(route(_low_confidence_text(), previous=None, classifier=UnavailableClassifier()))
    assert decision.kind is None
    assert decision.tier2_outcome == Tier2Outcome.UNAVAILABLE
