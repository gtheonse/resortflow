"""Feature 010, T006 — `observability.telemetry.annotate_routing` sets exactly
the seven `resortflow.routing.*` attributes (attributes.py's Amendment — Feature
010) and nothing else: no guest message text, no free-form services string
beyond the decision's own bounded list.

Uses a small duck-typed fake instead of `graph.routing.router.RoutingDecision`
(landed later, US1) — `annotate_routing` takes `Any` precisely so
`observability/` never depends on `graph/` (mirrors `PromptLineage`'s own
Protocol-based decoupling in `observability/telemetry.py`)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pytest

from observability import telemetry
from observability.attributes import (
    ROUTING_AMBIGUOUS,
    ROUTING_CARRIED_OVER,
    ROUTING_CONFIDENCE,
    ROUTING_KIND,
    ROUTING_SERVICES,
    ROUTING_TIER,
    ROUTING_TIER2_OUTCOME,
)

_ROUTING_ATTRS = frozenset(
    {
        ROUTING_KIND,
        ROUTING_SERVICES,
        ROUTING_TIER,
        ROUTING_CONFIDENCE,
        ROUTING_AMBIGUOUS,
        ROUTING_CARRIED_OVER,
        ROUTING_TIER2_OUTCOME,
    }
)


class _FakeKind(str, Enum):
    BOOK = "book"


class _FakeDomain(str, Enum):
    DINING = "dining"
    ENTERTAINMENT = "entertainment"


class _FakeTier2Outcome(str, Enum):
    NOT_CALLED = "not_called"


@dataclass
class _FakeDecision:
    kind: _FakeKind | None
    services: list[_FakeDomain]
    tier: int
    confidence: float
    ambiguous: bool
    carried_over: bool
    tier2_outcome: _FakeTier2Outcome


@pytest.fixture(autouse=True)
def _isolated_tracer():
    exporter = telemetry.use_in_memory_exporter_for_tests()
    yield exporter
    telemetry.reset_for_tests()


def _decision(**overrides) -> _FakeDecision:
    base = {
        "kind": _FakeKind.BOOK,
        "services": [_FakeDomain.ENTERTAINMENT, _FakeDomain.DINING],
        "tier": 1,
        "confidence": 0.92,
        "ambiguous": False,
        "carried_over": False,
        "tier2_outcome": _FakeTier2Outcome.NOT_CALLED,
    }
    base.update(overrides)
    return _FakeDecision(**base)


def test_annotate_routing_sets_exactly_the_seven_attributes(_isolated_tracer):
    with telemetry.start_agent_span("intent_router", node="intent_router") as span:
        telemetry.annotate_routing(span, _decision())
    (finished,) = _isolated_tracer.get_finished_spans()
    attrs = dict(finished.attributes)
    present = {k for k in attrs if k.startswith("resortflow.routing.")}
    assert present == _ROUTING_ATTRS


def test_annotate_routing_services_is_sorted_bounded_list_not_free_text(_isolated_tracer):
    with telemetry.start_agent_span("intent_router", node="intent_router") as span:
        telemetry.annotate_routing(span, _decision())
    (finished,) = _isolated_tracer.get_finished_spans()
    assert dict(finished.attributes)[ROUTING_SERVICES] == "dining,entertainment"


def test_annotate_routing_never_writes_message_text_anywhere_on_the_span(_isolated_tracer):
    with telemetry.start_agent_span("intent_router", node="intent_router") as span:
        telemetry.annotate_routing(span, _decision())
    (finished,) = _isolated_tracer.get_finished_spans()
    for value in dict(finished.attributes).values():
        assert "guest" not in str(value).lower()
        assert "cancle" not in str(value).lower()


def test_annotate_routing_undecided_kind_reports_none(_isolated_tracer):
    with telemetry.start_agent_span("intent_router", node="intent_router") as span:
        telemetry.annotate_routing(
            span,
            _decision(kind=None, services=[], confidence=0.0),
        )
    (finished,) = _isolated_tracer.get_finished_spans()
    assert dict(finished.attributes)[ROUTING_KIND] == "undecided"
    assert dict(finished.attributes)[ROUTING_SERVICES] == ""


def test_a_real_browse_decision_is_recorded_as_kind_browse_with_no_filters_or_dates(_isolated_tracer):
    """Feature 011, FR-025 / quickstart scenario 10 (offline half): the bounded `resortflow.routing.kind` gains the value
    `browse`; the filters and date window the decision carries (the guest's own words, resolved) are NOT written to
    the span, and no new `resortflow.routing.*` attribute appears."""
    from datetime import date

    from graph.models import BrowseFilters, DateWindow, Domain, RequestKind, RoutingDecision

    decision = RoutingDecision(
        kind=RequestKind.BROWSE,
        services=[Domain.DINING],
        confidence=0.9,
        browse_filters=BrowseFilters(cuisine="japanese", price_band="$$"),
        window=DateWindow(start=date(2026, 10, 5), end=date(2026, 10, 8), source="stated"),
    )
    with telemetry.start_agent_span("intent_router", node="intent_router") as span:
        telemetry.annotate_routing(span, decision)
    (finished,) = _isolated_tracer.get_finished_spans()
    attrs = dict(finished.attributes)
    assert attrs[ROUTING_KIND] == "browse"
    assert {k for k in attrs if k.startswith("resortflow.routing.")} == _ROUTING_ATTRS
    rendered = " ".join(str(v).lower() for v in attrs.values())
    assert "japanese" not in rendered and "2026-10" not in rendered
