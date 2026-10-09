"""Feature 010, T039 — tier 1: deterministic request-kind classification
(research R-06), the FR-020 clause split, and the one-turn follow-up
carry-over (research R-09, analyze finding I1's `PreviousTurn`)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from graph.models import (
    BrowseFilters,
    DateWindow,
    Domain,
    RequestKind,
    RoutingDecision,
    RoutingTier,
)
from graph.routing._distance import within_tolerance
from graph.routing.browse import extract_filters, resolve_window, wants_stay_period
from graph.routing.matcher import ServiceMatch, match_services, phrase_present, tokenize
from graph.routing.temporal import parse_date_range
from graph.routing.vocabulary import DEFAULT, Vocabulary

_CLAUSE_SPLIT_RE = re.compile(r",\s*and\b|\band\s+also\b|;|\bthen\b|\balso\b", re.IGNORECASE)

_EXACT_CONFIDENCE = 0.9
_TYPO_CONFIDENCE = 0.75
_LOW_CONFIDENCE = 0.0

_LOOKUP_QUESTION_STARTERS = frozenset(
    {"what", "whats", "is", "are", "am", "when", "how", "why", "did", "do", "does", "was", "were"}
)
"""Guards the possessive-`my`-implies-lookup heuristic in `_classify_single`
below: found live (demo report) that without this, "the shower in my room
is broken" (a maintenance complaint, correctly `book`) got hijacked by the
same rule meant for "what time is my dinner reservation?" — the
distinguishing signal a full parser would use (is this actually a
question about a booking, or a declarative complaint that merely contains
"my") is approximated here by requiring the message to *start with* an
interrogative word, true for every intended target case and false for the
complaint."""


@dataclass
class PreviousTurn:
    """What the router-readable state from the previous turn contributes
    to this turn's carry-over eligibility (research R-09). Constructed by
    `INTENT_ROUTER` from the previous `routing`/`turn_baseline`/
    `booking_view` — never from message text."""

    kind: RequestKind | None
    services: list[Domain] = field(default_factory=list)
    had_booking: bool = False
    """True only when the previous turn actually produced or matched a
    booking record — a `book`/`lookup` decision alone is not enough."""
    focus_keys: list[str] = field(default_factory=list)
    """The specific booking identities the previous turn produced (a new
    confirmation/hold) or matched (a lookup) — carried onto the new
    decision so `MODIFY_REQUEST` can identify the booking without
    re-deriving it (US4)."""
    filters: BrowseFilters | None = None
    """Feature 011: the previous browse's filters, read from the previous `RoutingDecision` (never message text)."""
    window: DateWindow | None = None
    """Feature 011: the previous browse's date window, likewise."""


def _kind_cfg(vocab: Vocabulary, name: str) -> dict[str, Any]:
    cfg = vocab.kinds.get(name, {})
    return cfg if isinstance(cfg, dict) else {}


def _matches_words_or_phrases(
    tokens: list[str], words: list[str], phrases: list[str], *, vocab: Vocabulary = DEFAULT
) -> bool:
    """Kind-cue words tolerate typos exactly like service vocabulary does
    (FR-002, per the clarify session: tolerance covers request-kind
    phrasing too, e.g. "cancle") — phrases stay exact, the same
    no-typo-tolerance-across-a-phrase-boundary rule `matcher.py` applies to
    service phrases."""
    for token in tokens:
        if token in words:
            return True
        for word in words:
            if within_tolerance(token, word, tolerance=vocab.tolerance):
                return True
    return any(phrase_present(tokens, p) for p in phrases)


# --- Feature 011: browse recognition (contracts/browse-router-api.md) --------------------------------------


def _exact_hit(tokens: list[str], words: list[str], phrases: list[str] | None = None) -> bool:
    """Exact whole-token / contiguous-phrase match, no typo tolerance: browse nouns are plural set-denoting
    forms and tolerance would let `shoes` pass for `shows` (feature 010's collision lesson). Typos are covered
    by the service vocabulary's own misspelling lists."""
    token_set = set(tokens)
    if any(w in token_set for w in words):
        return True
    return any(phrase_present(tokens, p) for p in (phrases or []))


def _browse_nouns(tokens: list[str], cfg: dict[str, Any]) -> dict[str, bool]:
    """Which noun groups appear: keys are service names, `games`, `none` (overview nouns)."""
    nouns = cfg.get("nouns", {})
    found: dict[str, bool] = {}
    for group, words in nouns.items():
        singles = [w for w in words if " " not in w]
        multis = [w for w in words if " " in w]
        found[group] = _exact_hit(tokens, singles, multis)
    return found


def _browse_matches(tokens: list[str], cfg: dict[str, Any], has_book_cue: bool) -> tuple[bool, dict[str, bool]]:
    """(is_browse, noun groups found). Vetoes first; then rule 1 (cue + noun), rule 2 (noun + stay-period
    marker, no cue), rule 3 (strong phrase alone)."""
    if not cfg or has_book_cue:
        return False, {}
    vetoes = cfg.get("vetoes", {})
    if _exact_hit(tokens, vetoes.get("attribute_words", []), vetoes.get("attribute_phrases", [])):
        return False, {}
    found = _browse_nouns(tokens, cfg)
    any_noun = any(found.values())
    cues = cfg.get("cues", {})
    has_cue = _exact_hit(tokens, cues.get("words", []), cues.get("phrases", []))
    if has_cue and any_noun:
        return True, found
    if any_noun and wants_stay_period(" ".join(tokens)):
        return True, found
    if _exact_hit(tokens, [], cfg.get("strong_phrases", [])):
        return True, found
    return False, found


def _browse_services(
    tokens: list[str], services: ServiceMatch, noun_groups: dict[str, bool], cfg: dict[str, Any], stay_marker: bool
) -> list[Domain]:
    """The services a browse concerns (noun -> service map, `games` collision, "my stay" is a window marker)."""
    base: list[Domain] = list(services.services)
    if stay_marker and not noun_groups.get("stay"):
        base = [d for d in base if d != Domain.STAY]  # "my stay" names a window, not the stay service
    if noun_groups.get("games"):
        casino = _exact_hit(tokens, cfg.get("casino_words", []))
        if casino:
            base = [Domain.GAMING]
        elif Domain.ENTERTAINMENT not in base:
            base.append(Domain.ENTERTAINMENT)
    if not base:
        for group in ("dining", "entertainment", "stay", "wellness"):
            if noun_groups.get(group):
                base.append(Domain(group))
    if not base and _exact_hit(tokens, [], cfg.get("events_default_phrases", [])):
        base = [Domain.ENTERTAINMENT]
    if Domain.GAMING in base and len(base) > 1:
        base = [d for d in base if d != Domain.GAMING]  # gaming only ever browses alone (the gated path)
    return list(dict.fromkeys(base))


def _non_empty(filters: BrowseFilters) -> BrowseFilters | None:
    return None if filters == BrowseFilters() else filters


def _browse_decision(
    text: str, tokens: list[str], services: ServiceMatch, noun_groups: dict[str, bool], cfg: dict[str, Any],
    today: date,
) -> RoutingDecision:
    if services.ambiguous:
        return RoutingDecision(kind=None, confidence=_LOW_CONFIDENCE, tier=RoutingTier.KIND_RULES, ambiguous=True)
    stay_marker = wants_stay_period(text)
    chosen = _browse_services(tokens, services, noun_groups, cfg, stay_marker)
    confidence = _EXACT_CONFIDENCE if services.exact else _TYPO_CONFIDENCE
    return RoutingDecision(
        kind=RequestKind.BROWSE,
        services=chosen,
        tier=RoutingTier.KIND_RULES,
        confidence=confidence,
        browse_filters=_non_empty(extract_filters(text)),
        window=resolve_window(text, today=today, wants_stay_period=stay_marker),
    )


def _classify_single(
    text: str, services: ServiceMatch, vocab: Vocabulary, today: date | None = None
) -> RoutingDecision:
    """Tier 1 precedence for one clause, no carry-over (research R-06):
    out_of_scope > modify > lookup > book > smalltalk > faq > undecided."""
    tokens = tokenize(text)

    oos_cfg = vocab.out_of_scope
    if _matches_words_or_phrases(tokens, oos_cfg.words, oos_cfg.phrases, vocab=vocab):
        return RoutingDecision(
            kind=RequestKind.OUT_OF_SCOPE, services=[], tier=RoutingTier.KIND_RULES, confidence=_EXACT_CONFIDENCE
        )

    modify_cfg = _kind_cfg(vocab, "modify")
    has_modify = _matches_words_or_phrases(tokens, modify_cfg.get("words", []), modify_cfg.get("phrases", []), vocab=vocab)
    if has_modify:
        # A modify request naming >= 2 distinct services in one clause is
        # not a single unambiguous "change this booking" — it is more
        # likely two different actions run together ("cancel my dinner
        # and book a spa instead"), which this feature declines to guess
        # at (spec.md's own "if the parts cannot be separated, the
        # clarifying question MUST be used").
        if len(services.services) >= 2:
            return RoutingDecision(kind=None, confidence=_LOW_CONFIDENCE, tier=RoutingTier.KIND_RULES)
        svc: list[Domain] = [s for s in services.services if s != Domain.GAMING]
        confidence = _EXACT_CONFIDENCE if services.exact else _TYPO_CONFIDENCE
        return RoutingDecision(
            kind=RequestKind.MODIFY, services=svc, tier=RoutingTier.KIND_RULES, confidence=confidence
        )

    lookup_cfg = _kind_cfg(vocab, "lookup")
    if _matches_words_or_phrases(
        tokens, lookup_cfg.get("words", []), lookup_cfg.get("phrases", []), vocab=vocab
    ):
        return RoutingDecision(
            kind=RequestKind.LOOKUP, services=services.services, tier=RoutingTier.KIND_RULES,
            confidence=_EXACT_CONFIDENCE,
        )

    book_cfg = _kind_cfg(vocab, "book")
    has_book_cue = _matches_words_or_phrases(tokens, book_cfg.get("words", []), book_cfg.get("phrases", []), vocab=vocab)

    # Feature 011 (FR-001..FR-004, research R-02): browse sits above the possessive-lookup rule below (that rule
    # turned "events in my stay period" into a booking lookup) and above FAQ (which sent "what are the dining
    # venues" to the knowledge base). Vetoes: an explicit book cue, or an attribute question about one item.
    browse_cfg = _kind_cfg(vocab, "browse")
    is_browse, noun_groups = _browse_matches(tokens, browse_cfg, has_book_cue)
    if is_browse:
        return _browse_decision(text, tokens, services, noun_groups, browse_cfg, today or date.today())  # noqa: DTZ011 - local wall-clock date, as elsewhere

    # FR-007: a possessive "my" combined with a recognised service and no
    # explicit booking verb is a status reference to an existing booking
    # ("what time is my dinner reservation?", "what's my loyalty tier?"),
    # not a new request — found live (demo report) that these fell through
    # to `book` (a bare service word, no lookup *phrase* matched literally)
    # since nothing here checked for the possessive on its own. Checked
    # before the book-cue branch below so an explicit booking verb still
    # wins ("book my favorite table" stays `book`).
    if (
        not has_book_cue
        and services.services
        and "my" in tokens
        and tokens
        and tokens[0] in _LOOKUP_QUESTION_STARTERS
    ):
        if services.ambiguous:
            return RoutingDecision(kind=None, confidence=_LOW_CONFIDENCE, tier=RoutingTier.KIND_RULES, ambiguous=True)
        confidence = _EXACT_CONFIDENCE if services.exact else _TYPO_CONFIDENCE
        return RoutingDecision(
            kind=RequestKind.LOOKUP, services=services.services, tier=RoutingTier.KIND_RULES, confidence=confidence
        )

    if has_book_cue:
        if services.ambiguous:
            return RoutingDecision(kind=None, confidence=_LOW_CONFIDENCE, tier=RoutingTier.KIND_RULES, ambiguous=True)
        confidence = _EXACT_CONFIDENCE if services.exact else _TYPO_CONFIDENCE
        return RoutingDecision(
            kind=RequestKind.BOOK, services=services.services, tier=RoutingTier.KIND_RULES, confidence=confidence
        )

    smalltalk_cfg = _kind_cfg(vocab, "smalltalk")
    max_tokens = smalltalk_cfg.get("max_tokens", 4)
    if len(tokens) <= max_tokens and _matches_words_or_phrases(
        tokens, smalltalk_cfg.get("words", []), smalltalk_cfg.get("phrases", []), vocab=vocab
    ):
        return RoutingDecision(
            kind=RequestKind.SMALLTALK, services=[], tier=RoutingTier.KIND_RULES, confidence=_EXACT_CONFIDENCE
        )

    # FAQ is checked here, before the bare "a service word was mentioned"
    # fallback below — found live (demo report): "whats the late chekout
    # policy", "is there a gym on site?", "do you have connecting rooms?"
    # all carry a clear FAQ phrase/word *and* a service word, and were
    # being silently claimed by the bare-service-implies-book fallback
    # before FAQ ever got a chance. An explicit book cue (checked above)
    # still wins over an incidental FAQ-phrase overlap either way.
    faq_cfg = _kind_cfg(vocab, "faq")
    if _matches_words_or_phrases(tokens, faq_cfg.get("words", []), faq_cfg.get("phrases", []), vocab=vocab):
        return RoutingDecision(
            kind=RequestKind.FAQ, services=services.services, tier=RoutingTier.KIND_RULES,
            confidence=_EXACT_CONFIDENCE - 0.05,
        )

    if services.services:
        # A bare service mention with no book cue, no possessive lookup
        # signal and no FAQ phrasing at all ("a table for 4 tonight") —
        # still assumed to be a new booking, same as before this feature's
        # T080/demo-report fixes split this branch apart.
        if services.ambiguous:
            return RoutingDecision(kind=None, confidence=_LOW_CONFIDENCE, tier=RoutingTier.KIND_RULES, ambiguous=True)
        confidence = _EXACT_CONFIDENCE if services.exact else _TYPO_CONFIDENCE
        return RoutingDecision(
            kind=RequestKind.BOOK, services=services.services, tier=RoutingTier.KIND_RULES, confidence=confidence
        )

    return RoutingDecision(kind=None, confidence=_LOW_CONFIDENCE, tier=RoutingTier.KIND_RULES)


def _split_clauses(text: str) -> list[str]:
    parts = [p.strip() for p in _CLAUSE_SPLIT_RE.split(text) if p.strip()]
    return parts


def _merge_filters(previous: BrowseFilters | None, new: BrowseFilters) -> BrowseFilters | None:
    merged = {**(previous.model_dump() if previous else {}), **{k: v for k, v in new.model_dump().items() if v is not None}}
    return _non_empty(BrowseFilters(**merged))


def _try_browse_carry_over(
    text: str, tokens: list[str], previous: PreviousTurn, vocab: Vocabulary, today: date
) -> RoutingDecision | None:
    """Feature 011, FR-005a (research R-09): a short follow-up narrows the immediately previous browse
    ("only japanese", "cheaper ones", "what about this weekend"), or supplies the dates a `needs_dates` browse
    asked for. One turn only: `PreviousTurn` is rebuilt from the last decision every turn, so any intervening
    non-browse turn removes eligibility. Filters merge (new value wins per field); a new date replaces the window."""
    cfg = _kind_cfg(vocab, "browse")
    new_filters = extract_filters(text)
    new_range = parse_date_range(text, today=today)
    completes_dates = previous.window is not None and previous.window.source == "needs_dates" and new_range is not None
    narrowing = cfg.get("narrowing", {})
    has_narrowing_cue = _exact_hit(tokens, narrowing.get("words", []), narrowing.get("phrases", []))
    narrows = has_narrowing_cue and (new_filters != BrowseFilters() or new_range is not None)
    if not (narrows or completes_dates):
        return _try_browse_handoff(text, tokens, previous, vocab)
    window = previous.window
    if new_range is not None:
        window = DateWindow(start=new_range[0], end=new_range[1], source="stated")
    return RoutingDecision(
        kind=RequestKind.BROWSE,
        services=[s for s in previous.services],
        tier=RoutingTier.KIND_RULES,
        confidence=_EXACT_CONFIDENCE,
        carried_over=True,
        browse_filters=_merge_filters(previous.filters, new_filters),
        window=window,
    )


def _try_browse_handoff(
    text: str, tokens: list[str], previous: PreviousTurn, vocab: Vocabulary
) -> RoutingDecision | None:
    """Feature 011, SC-006 (found live): a booking request right after a browse ("book Sakura Sushi Bar for 2
    tomorrow at 7pm") names a venue or event, which is not a service word, so it would otherwise reach the generic
    clarifying question. It inherits the previous browse's bookable services instead. Only when the message has
    its own book cue and names no service itself; casino games are never bookable, so a gaming-only or overview
    browse hands nothing off. One turn only, like every carry-over: `PreviousTurn` is rebuilt every turn."""
    bookable = [s for s in previous.services if s != Domain.GAMING]
    if not bookable or match_services(text, vocab).services:
        return None
    book_cfg = _kind_cfg(vocab, "book")
    if not _matches_words_or_phrases(tokens, book_cfg.get("words", []), book_cfg.get("phrases", []), vocab=vocab):
        return None
    return RoutingDecision(
        kind=RequestKind.BOOK,
        services=bookable,
        tier=RoutingTier.KIND_RULES,
        confidence=_EXACT_CONFIDENCE,
        carried_over=True,
    )


def _try_carry_over(
    text: str, previous: PreviousTurn, vocab: Vocabulary, today: date | None = None
) -> RoutingDecision | None:
    """FR-008a: only when the previous turn eligibly booked/looked
    something up. Reference/status -> lookup; change wording -> modify;
    repeat wording -> book — all scoped to the previous turn's services.
    Feature 011: a previous `browse` carries over on its own terms (`_try_browse_carry_over`)."""
    if previous.kind == RequestKind.BROWSE:
        return _try_browse_carry_over(text, tokenize(text), previous, vocab, today or date.today())  # noqa: DTZ011 - local wall-clock date, as elsewhere
    if previous.kind not in (RequestKind.BOOK, RequestKind.LOOKUP) or not previous.had_booking:
        return None
    tokens = tokenize(text)
    followup_cfg = _kind_cfg(vocab, "followup")

    change_cfg = followup_cfg.get("change", {})
    if _matches_words_or_phrases(tokens, change_cfg.get("words", []), change_cfg.get("phrases", []), vocab=vocab):
        return RoutingDecision(
            kind=RequestKind.MODIFY, services=[s for s in previous.services if s != Domain.GAMING],
            tier=RoutingTier.KIND_RULES, confidence=_EXACT_CONFIDENCE, carried_over=True,
            focus_keys=previous.focus_keys,
        )

    repeat_cfg = followup_cfg.get("repeat", {})
    if _matches_words_or_phrases(tokens, repeat_cfg.get("words", []), repeat_cfg.get("phrases", []), vocab=vocab):
        return RoutingDecision(
            kind=RequestKind.BOOK, services=previous.services, tier=RoutingTier.KIND_RULES,
            confidence=_EXACT_CONFIDENCE, carried_over=True,
        )

    reference_cfg = followup_cfg.get("reference", {})
    if _matches_words_or_phrases(tokens, reference_cfg.get("words", []), reference_cfg.get("phrases", []), vocab=vocab):
        return RoutingDecision(
            kind=RequestKind.LOOKUP, services=previous.services, tier=RoutingTier.KIND_RULES,
            confidence=_EXACT_CONFIDENCE, carried_over=True,
        )
    return None


def classify_kind(
    text: str,
    services: ServiceMatch,
    *,
    previous: PreviousTurn | None = None,
    vocab: Vocabulary = DEFAULT,
    today: date | None = None,
) -> RoutingDecision:
    """Tier 1 entry point. Clause-splits for the FR-020 lookup+book mixed
    case first; a two-clause split whose kinds are exactly
    `{lookup, book}` combines into one decision with `secondary_kind`/
    `secondary_services`. Anything else that splits into two clauses
    without resolving to that pair is treated as unseparable (undecided).
    A single clause (the common case) goes straight to `_classify_single`,
    then — only if nothing was recognised at all — to one-turn carry-over
    (FR-008a: carry-over applies only when tiers 0/1 found no service and
    no kind signal)."""
    clauses = _split_clauses(text)
    if len(clauses) == 2:
        first_services = match_services(clauses[0], vocab)
        second_services = match_services(clauses[1], vocab)
        first = _classify_single(clauses[0], first_services, vocab, today)
        second = _classify_single(clauses[1], second_services, vocab, today)
        kinds_pair = {first.kind, second.kind}
        for primary_kind in (RequestKind.LOOKUP, RequestKind.BROWSE):
            if kinds_pair == {primary_kind, RequestKind.BOOK}:
                primary = first if first.kind == primary_kind else second
                book_decision = first if first.kind == RequestKind.BOOK else second
                return RoutingDecision(
                    kind=primary_kind,
                    services=primary.services,
                    secondary_kind=RequestKind.BOOK,
                    secondary_services=book_decision.services,
                    tier=RoutingTier.KIND_RULES,
                    confidence=_EXACT_CONFIDENCE,
                    browse_filters=primary.browse_filters,
                    window=primary.window,
                )
        if len(kinds_pair) == 1 and None not in kinds_pair:
            # Both "clauses" resolved to the SAME kind — this is not two
            # different actions run together, it is one request whose own
            # phrasing happens to contain a clause-boundary word ("dinner,
            # a show, and a spa treatment" is a plain multi-service list,
            # not a mixed intent). Merge rather than declare unseparable.
            (only_kind,) = kinds_pair
            merged_services: list[Domain] = list(dict.fromkeys(first.services + second.services))
            if only_kind == RequestKind.BROWSE:
                if Domain.GAMING in merged_services and len(merged_services) > 1:
                    merged_services = [d for d in merged_services if d != Domain.GAMING]
                return RoutingDecision(
                    kind=RequestKind.BROWSE,
                    services=merged_services,
                    tier=RoutingTier.KIND_RULES,
                    confidence=min(first.confidence, second.confidence),
                    browse_filters=first.browse_filters or second.browse_filters,
                    window=first.window or second.window,
                )
            return RoutingDecision(
                kind=only_kind,
                services=merged_services,
                tier=RoutingTier.KIND_RULES,
                confidence=min(first.confidence, second.confidence),
            )
        return RoutingDecision(kind=None, confidence=_LOW_CONFIDENCE, tier=RoutingTier.KIND_RULES)

    # Carry-over is tried *before* the single-clause classifier whenever a
    # previous turn is eligible (found live, demo report): a message like
    # "what time was that?" or "which restaurant was it?" contains a bare
    # word ("what"/"which") that `_classify_single` recognises on its own
    # (FAQ, via a generic cue) well before it would ever fall through to
    # "nothing recognised" — so carry-over, gated tightly by
    # `_try_carry_over` itself (previous.kind, `had_booking`, and one of
    # the specific followup reference/change/repeat cues), never got a
    # chance under the old "only after `_classify_single` finds nothing"
    # order. This changes behaviour only for messages `_try_carry_over`
    # would have accepted anyway; anything it declines (returns `None`
    # for) falls through to `_classify_single` exactly as before.
    if previous is not None:
        carried = _try_carry_over(text, previous, vocab, today)
        if carried is not None:
            return carried

    return _classify_single(text, services, vocab, today)
