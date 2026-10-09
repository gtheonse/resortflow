"""Feature 010, T037 — the routing vocabulary: schema, load-time validation,
and the cross-service collision check (research R-05,
`specs/010-tiered-intent-routing/data-model.md`'s vocabulary-file section).

Reviewable data (FR-004): every word/phrase/misspelling a service or a
request kind recognises lives in `vocabulary.yaml`, not in Python. This
module only loads and validates it; matching is `matcher.py`'s job."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graph.models import Domain
from graph.routing._distance import within_tolerance

VOCAB_PATH = Path(__file__).resolve().parent / "vocabulary.yaml"

_WORD_RE_MSG = "must be a single lowercase token (no spaces, no uppercase)"


class VocabularyError(ValueError):
    """The vocabulary file is malformed (schema-level)."""


class VocabularyCollisionError(ValueError):
    """Two services' vocabularies collide within each other's typo
    tolerance band (research R-05) — a load-time, not schema-level, check."""


class ToleranceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exact_max_len: int = 3
    swap_only_len: int = 4
    one_edit_min_len: int = 5
    two_edit_min_len: int = 8


class WordEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word: str
    variants: list[str] = Field(default_factory=list)
    weak: bool = False
    """Feature 011: a generic word (a booking verb such as `reserve`) that only counts toward a service when no
    other service was matched strongly, so "reserve a spa treatment" is wellness only."""

    @field_validator("word")
    @classmethod
    def _word_is_single_lowercase_token(cls, value: str) -> str:
        if not value or " " in value or value != value.lower() or not value.isalpha():
            raise ValueError(f"word {value!r} {_WORD_RE_MSG}")
        return value

    @field_validator("variants")
    @classmethod
    def _variants_are_single_lowercase_tokens(cls, value: list[str]) -> list[str]:
        for variant in value:
            if not variant or " " in variant or variant != variant.lower() or not variant.isalpha():
                raise ValueError(f"variant {variant!r} {_WORD_RE_MSG}")
        return value


class ServiceVocabulary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    words: list[WordEntry] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)
    misspellings: list[str] = Field(default_factory=list)
    exclude_phrases: list[str] = Field(default_factory=list)

    def all_words(self) -> list[str]:
        """Base words plus every variant, flattened — what the collision
        check and the matcher's whole-word pass both compare against."""
        out: list[str] = []
        for entry in self.words:
            out.append(entry.word)
            out.extend(entry.variants)
        return out

    def weak_words(self) -> set[str]:
        """Base words and variants of entries marked `weak` (see `WordEntry.weak`)."""
        out: set[str] = set()
        for entry in self.words:
            if entry.weak:
                out.add(entry.word)
                out.update(entry.variants)
        return out


class OutOfScopeVocabulary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    words: list[str] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)


class Vocabulary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    tolerance: ToleranceConfig = Field(default_factory=ToleranceConfig)
    services: dict[Domain, ServiceVocabulary] = Field(default_factory=dict)
    kinds: dict[str, Any] = Field(default_factory=dict)
    """Loosely typed on purpose: `kinds` nests unevenly (`followup` has its
    own sub-keys `reference`/`change`/`repeat`; the others are flat
    `{words, phrases, max_tokens}`) — `graph.routing.kinds` interprets this
    dict directly rather than this module owning a second, parallel schema
    for it."""
    out_of_scope: OutOfScopeVocabulary = Field(default_factory=OutOfScopeVocabulary)

    @model_validator(mode="after")
    def _no_duplicate_phrase_across_services(self) -> Vocabulary:
        seen: dict[str, Domain] = {}
        for domain, vocab in self.services.items():
            for phrase in vocab.phrases:
                if phrase in seen and seen[phrase] != domain:
                    raise ValueError(
                        f"phrase {phrase!r} is declared for both {seen[phrase]} and {domain}"
                    )
                seen[phrase] = domain
        return self


def _check_collisions(vocab: Vocabulary) -> None:
    """Research R-05: no word of service A may fall within service B's
    typo tolerance band, or vice versa — ambiguity (FR-005) must come only
    from a genuine typo equidistant between two real vocabulary entries at
    matcher run-time, never from the vocabulary conflicting with itself."""
    per_service: dict[Domain, list[str]] = {
        domain: sv.all_words() for domain, sv in vocab.services.items()
    }
    domains = list(per_service)
    for a_idx, domain_a in enumerate(domains):
        for domain_b in domains[a_idx + 1 :]:
            for word_a in per_service[domain_a]:
                for word_b in per_service[domain_b]:
                    if within_tolerance(word_a, word_b, tolerance=vocab.tolerance) or within_tolerance(
                        word_b, word_a, tolerance=vocab.tolerance
                    ):
                        raise VocabularyCollisionError(
                            f"{domain_a}:{word_a!r} collides with {domain_b}:{word_b!r} "
                            "within the configured typo tolerance"
                        )


def load_vocabulary(
    path: Path | None = None, *, data: dict[str, Any] | None = None
) -> Vocabulary:
    """Loads and validates a vocabulary — from `data` directly (tests), or
    from a YAML file (`path`, defaulting to the shipped `vocabulary.yaml`).
    Raises `VocabularyCollisionError` if the collision check fails; a
    schema violation raises Pydantic's `ValidationError` directly (not
    wrapped), so callers can tell the two failure classes apart."""
    if data is None:
        file_path = path or VOCAB_PATH
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    vocab = Vocabulary.model_validate(data)
    _check_collisions(vocab)
    return vocab


DEFAULT: Vocabulary = load_vocabulary()
"""Loaded and validated at import time — a broken shipped vocabulary must
fail the process at startup, not on the first guest turn (same fail-fast
posture as `prompts.registry.validate_all`)."""
