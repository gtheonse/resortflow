"""Feature 010, T032 — `graph.routing.kinds.classify_kind`: tier 1's
precedence order (research R-06), the FR-020 clause split, and the
one-turn follow-up carry-over (research R-09). Must fail before T039."""

from __future__ import annotations

from graph.models import Domain, RequestKind, RoutingDecision
from graph.routing.kinds import PreviousTurn, classify_kind
from graph.routing.matcher import match_services


def _classify(text: str, *, previous: PreviousTurn | None = None) -> RoutingDecision:
    services = match_services(text)
    return classify_kind(text, services, previous=previous)


def test_out_of_scope_beats_a_service_match() -> None:
    decision = _classify("book a flight with dinner on board")
    assert decision.kind == RequestKind.OUT_OF_SCOPE
    assert decision.services == []


def test_modify_beats_book() -> None:
    decision = _classify("cancle my dinner")
    assert decision.kind == RequestKind.MODIFY
    assert Domain.DINING in decision.services


def test_lookup_phrasing() -> None:
    decision = _classify("what is the restaurent i got reserved for sep 30, 8 PM?")
    assert decision.kind == RequestKind.LOOKUP


def test_smalltalk_at_most_a_few_tokens() -> None:
    decision = _classify("thanks")
    assert decision.kind == RequestKind.SMALLTALK
    assert decision.services == []


def test_smalltalk_does_not_fire_on_a_long_message() -> None:
    decision = _classify("thanks for the info, but I actually wanted to book a table for four")
    assert decision.kind != RequestKind.SMALLTALK


def test_faq_question_form_with_no_service_signal() -> None:
    decision = _classify("what time does the pool close?")
    assert decision.kind == RequestKind.FAQ
    assert decision.services == []


def test_bare_service_mention_means_book() -> None:
    decision = _classify("I'd like a masage at the spa")
    assert decision.kind == RequestKind.BOOK
    assert Domain.WELLNESS in decision.services


def test_mixed_lookup_and_book_clause_split() -> None:
    decision = _classify("what did I book, and add a spa for tomorrow")
    assert decision.kind == RequestKind.LOOKUP
    assert decision.secondary_kind == RequestKind.BOOK
    assert Domain.WELLNESS in decision.secondary_services


def test_unseparable_mix_is_undecided() -> None:
    decision = _classify("cancel my dinner and book a spa instead")
    assert decision.kind is None


# --- Follow-up carry-over (research R-09) -----------------------------------


def _previous(kind: RequestKind, services: list[Domain], *, had_booking: bool = True) -> PreviousTurn:
    return PreviousTurn(kind=kind, services=services, had_booking=had_booking)


def test_reference_pattern_carries_over_to_lookup() -> None:
    prev = _previous(RequestKind.BOOK, [Domain.DINING])
    decision = _classify("is it confirmed?", previous=prev)
    assert decision.kind == RequestKind.LOOKUP
    assert decision.carried_over is True
    assert Domain.DINING in decision.services


def test_change_pattern_carries_over_to_modify() -> None:
    prev = _previous(RequestKind.BOOK, [Domain.DINING])
    decision = _classify("make it 6 people", previous=prev)
    assert decision.kind == RequestKind.MODIFY
    assert decision.carried_over is True


def test_repeat_pattern_carries_over_to_book() -> None:
    prev = _previous(RequestKind.BOOK, [Domain.DINING])
    decision = _classify("same again for tomorrow", previous=prev)
    assert decision.kind == RequestKind.BOOK
    assert decision.carried_over is True
    assert Domain.DINING in decision.services


def test_no_carry_over_after_faq() -> None:
    prev = _previous(RequestKind.FAQ, [], had_booking=False)
    decision = _classify("is it confirmed?", previous=prev)
    assert decision.carried_over is False


def test_no_carry_over_after_smalltalk() -> None:
    prev = _previous(RequestKind.SMALLTALK, [], had_booking=False)
    decision = _classify("is it confirmed?", previous=prev)
    assert decision.carried_over is False


def test_no_carry_over_after_out_of_scope() -> None:
    prev = _previous(RequestKind.OUT_OF_SCOPE, [], had_booking=False)
    decision = _classify("is it confirmed?", previous=prev)
    assert decision.carried_over is False


def test_no_carry_over_when_message_has_its_own_signal() -> None:
    """A follow-up-shaped message that already names its own service
    should not need carry-over at all — this just proves carry-over
    doesn't override an unambiguous message."""
    prev = _previous(RequestKind.BOOK, [Domain.DINING])
    decision = _classify("book a spa treatment for tomorrow", previous=prev)
    assert Domain.WELLNESS in decision.services
    assert decision.carried_over is False
