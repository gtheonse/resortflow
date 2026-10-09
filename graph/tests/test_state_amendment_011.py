"""Feature 011, T008 — the routing-model amendment (ADR-060,
`specs/011-browse-requests/contracts/state-schema-amendment-011.md`): the `browse` kind plus optional
`browse_filters`/`window` on `RoutingDecision`; no new `ResortFlowState` key.

Written first; must fail before T010 (`BrowseFilters`/`DateWindow`, the `RoutingDecision` fields and
invariants) and T011 (serializer allowlist, `GRAPH_VERSION` bump) exist."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from agents.dining.serde import EncryptedSerializer, plain_serde
from graph.models import (
    BrowseFilters,
    DateWindow,
    Domain,
    RequestKind,
    RoutingDecision,
    RoutingTier,
    Tier2Outcome,
)
from graph.state import ResortFlowState
from graph.supervisor import GRAPH_VERSION

# --- Version and key set ------------------------------------------------------


def test_graph_version_is_5_2() -> None:
    assert GRAPH_VERSION == "5.2"


def test_resortflowstate_key_count_is_unchanged() -> None:
    """Feature 011 adds no `ResortFlowState` key (the amendment lives inside `RoutingDecision`)."""
    assert len(ResortFlowState.__annotations__) == 25


# --- Resume safety: a pre-011 (5.1) routing value has neither new field ------------


def test_a_pre_011_decision_loads_with_both_new_fields_none() -> None:
    legacy = {
        "kind": "book", "services": ["dining"], "secondary_kind": None, "secondary_services": [],
        "tier": RoutingTier.KIND_RULES, "confidence": 0.9, "ambiguous": False, "carried_over": False,
        "focus_keys": [], "tier2_outcome": Tier2Outcome.NOT_CALLED,
    }
    decision = RoutingDecision.model_validate(legacy)
    assert decision.browse_filters is None
    assert decision.window is None


# --- Round trip through both serializers, no pickle fallback ---------------------


def _browse_decision() -> RoutingDecision:
    return RoutingDecision(
        kind=RequestKind.BROWSE,
        services=[Domain.ENTERTAINMENT],
        tier=RoutingTier.KIND_RULES,
        confidence=0.9,
        browse_filters=BrowseFilters(cuisine=None, category="concert", party_size=2),
        window=DateWindow(start=date(2026, 10, 5), end=date(2026, 10, 9), source="stated"),
    )


@pytest.mark.parametrize(
    "serializer_factory",
    [lambda: plain_serde(), lambda: EncryptedSerializer(key=b"0" * 32)],
    ids=["plain_serde", "EncryptedSerializer"],
)
def test_browse_decision_round_trips_with_no_pickle_fallback(serializer_factory) -> None:
    serde = serializer_factory()
    decision = _browse_decision()
    assert serde.loads_typed(serde.dumps_typed(decision)) == decision
    filters = decision.browse_filters
    window = decision.window
    assert serde.loads_typed(serde.dumps_typed(filters)) == filters
    assert serde.loads_typed(serde.dumps_typed(window)) == window


# --- Invariants ---------------------------------------------------------------------


def test_the_new_fields_are_only_valid_on_a_browse_decision() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(
            kind=RequestKind.BOOK, services=[Domain.DINING], tier=RoutingTier.KIND_RULES, confidence=0.9,
            browse_filters=BrowseFilters(cuisine="japanese"),
        )
    with pytest.raises(ValidationError):
        RoutingDecision(
            kind=RequestKind.FAQ, services=[], tier=RoutingTier.KIND_RULES, confidence=0.9,
            window=DateWindow(start=date(2026, 10, 5), end=date(2026, 10, 5), source="stated"),
        )


def test_gaming_may_appear_in_a_browse_decision_only_alone() -> None:
    RoutingDecision(kind=RequestKind.BROWSE, services=[Domain.GAMING], tier=RoutingTier.KIND_RULES, confidence=0.9)
    with pytest.raises(ValidationError):
        RoutingDecision(
            kind=RequestKind.BROWSE, services=[Domain.GAMING, Domain.DINING], tier=RoutingTier.KIND_RULES,
            confidence=0.9,
        )


def test_secondary_book_is_allowed_under_lookup_or_browse_and_nothing_else() -> None:
    for primary in (RequestKind.LOOKUP, RequestKind.BROWSE):
        decision = RoutingDecision(
            kind=primary, services=[Domain.DINING], secondary_kind=RequestKind.BOOK,
            secondary_services=[Domain.ENTERTAINMENT], tier=RoutingTier.KIND_RULES, confidence=0.9,
        )
        assert decision.secondary_kind == RequestKind.BOOK
    with pytest.raises(ValidationError):
        RoutingDecision(
            kind=RequestKind.FAQ, services=[], secondary_kind=RequestKind.BOOK,
            tier=RoutingTier.KIND_RULES, confidence=0.9,
        )
    with pytest.raises(ValidationError):
        RoutingDecision(
            kind=RequestKind.BROWSE, services=[Domain.DINING], secondary_kind=RequestKind.LOOKUP,
            tier=RoutingTier.KIND_RULES, confidence=0.9,
        )


def test_window_end_may_not_precede_start_and_source_is_a_closed_set() -> None:
    with pytest.raises(ValidationError):
        DateWindow(start=date(2026, 10, 9), end=date(2026, 10, 5), source="stated")
    with pytest.raises(ValidationError):
        DateWindow(start=date(2026, 10, 5), end=date(2026, 10, 9), source="guessed")  # type: ignore[arg-type]
    for source in ("stated", "default", "stay_period", "needs_dates"):
        DateWindow(start=date(2026, 10, 5), end=date(2026, 10, 5), source=source)


def test_browse_filters_are_all_optional_and_dietary_is_the_enum() -> None:
    assert BrowseFilters() == BrowseFilters(cuisine=None, price_band=None, dietary=None, category=None, party_size=None)
    with pytest.raises(ValidationError):
        BrowseFilters(dietary="gluten-free-ish")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        BrowseFilters(unexpected="x")  # type: ignore[call-arg]
