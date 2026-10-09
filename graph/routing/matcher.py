"""Feature 010, T038 — tier 0: tolerant whole-word and phrase matching
(research R-03, contracts/router-api.md). Pure, synchronous, no I/O
(Constitution Principle I)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from graph.models import Domain
from graph.routing._distance import edit_distance, within_tolerance
from graph.routing.vocabulary import DEFAULT, Vocabulary

__all__ = ["ServiceMatch", "edit_distance", "match_services", "phrase_present", "tokenize"]

_TOKEN_RE = re.compile(r"[a-z]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class ServiceMatch:
    services: list[Domain] = field(default_factory=list)
    exact: bool = True
    """False if any match was via typo tolerance rather than an exact
    word/phrase/misspelling hit."""
    ambiguous: bool = False
    """FR-005: a typo equidistant from ≥ 2 services — chooses neither."""
    excluded: list[str] = field(default_factory=list)
    """`exclude_phrases` that suppressed a would-be match, for debugging
    (`graph.routing.explain`)."""


def tokenize(text: str) -> list[str]:
    """Public re-export of the tokenizer, so `graph.routing.kinds` applies
    the identical lowercase-alpha tokenization to both the guest message
    and every kind/phrase cue it compares against."""
    return _tokenize(text)


def phrase_present(tokens: list[str], phrase: str) -> bool:
    """Public re-export — same tokenized-contiguous-subsequence check
    `match_services` uses for service phrases, reused by `kinds.py` for
    kind-cue phrases."""
    return _phrase_present(tokens, phrase)


def _phrase_present(tokens: list[str], phrase: str) -> bool:
    phrase_tokens = phrase.lower().split()
    n = len(phrase_tokens)
    if n == 0:
        return False
    return any(tokens[i : i + n] == phrase_tokens for i in range(len(tokens) - n + 1))


def match_services(text: str, vocab: Vocabulary = DEFAULT) -> ServiceMatch:
    """Whole-word, tolerant service matching. Never matches a vocabulary
    word as a mere substring of a longer token (the defect this feature
    exists to close) — every comparison is token-for-token or
    phrase-of-tokens, never `in` on the raw string."""
    tokens = _tokenize(text)
    if not tokens:
        return ServiceMatch()

    excluded: list[str] = []
    for domain, service_vocab in vocab.services.items():
        for phrase in service_vocab.exclude_phrases:
            if _phrase_present(tokens, phrase):
                excluded.append(phrase)

    def is_excluded_for(domain: object) -> bool:
        service_vocab = vocab.services[domain]  # type: ignore[index]
        return any(_phrase_present(tokens, p) for p in service_vocab.exclude_phrases)

    # (word_len, best_distance) per matched domain, for the ambiguity check —
    # only populated for TYPO matches (distance > 0); an exact hit never
    # competes on ambiguity with anything (FR-005 is specifically about
    # typos, not about a message that legitimately names two services,
    # which multi_service cases rely on being non-ambiguous).
    exact_hits: set[Domain] = set()
    weak_hits: set[Domain] = set()
    typo_candidates: dict[Domain, int] = {}

    for domain, service_vocab in vocab.services.items():
        if is_excluded_for(domain):
            continue
        # Phrases: exact multi-token match only (no typo tolerance across
        # a phrase boundary — phrases exist precisely for words too short
        # or too ambiguous to tolerate typos on their own).
        for phrase in service_vocab.phrases:
            if _phrase_present(tokens, phrase):
                exact_hits.add(domain)
        # Misspellings: exact match against the token stream.
        misspelling_set = {m.lower() for m in service_vocab.misspellings}
        if misspelling_set & set(tokens):
            exact_hits.add(domain)
        # Words + variants: exact, then typo-tolerant. Candidates are
        # pre-filtered by length (a tolerated typo differs by at most 2
        # characters, research R-03) before ever calling `within_tolerance`
        # — SC-007's 5 ms budget depends on comparing a token against a
        # handful of same-length-ish words, not the whole vocabulary.
        vocab_words = service_vocab.all_words()
        vocab_word_set = set(vocab_words)
        weak_word_set = service_vocab.weak_words()
        for token in tokens:
            if token in vocab_word_set:
                (weak_hits if token in weak_word_set else exact_hits).add(domain)
                continue
            token_len = len(token)
            candidates = [w for w in vocab_words if abs(len(w) - token_len) <= 2]
            for candidate in candidates:
                if within_tolerance(token, candidate, tolerance=vocab.tolerance):
                    typo_candidates[domain] = 1
                    break

    # A weak word (a generic booking verb) only counts when nothing was matched strongly (feature 011).
    if not exact_hits and weak_hits:
        exact_hits = weak_hits
    if exact_hits:
        return ServiceMatch(services=sorted(exact_hits, key=lambda d: d.value), exact=True, excluded=excluded)

    if len(typo_candidates) >= 2:
        return ServiceMatch(services=[], exact=False, ambiguous=True, excluded=excluded)
    if len(typo_candidates) == 1:
        (domain,) = typo_candidates
        return ServiceMatch(services=[domain], exact=False, excluded=excluded)
    return ServiceMatch(services=[], excluded=excluded)
