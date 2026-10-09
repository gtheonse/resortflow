"""Feature 010, T031 — `graph.routing.vocabulary`: schema validation and the
load-time cross-service collision check (research R-05). Must fail before
T037."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graph.models import Domain
from graph.routing.vocabulary import (
    DEFAULT,
    Vocabulary,
    VocabularyCollisionError,
    load_vocabulary,
)


def test_shipped_vocabulary_loads_with_zero_collisions() -> None:
    assert DEFAULT is not None
    assert Domain.DINING in DEFAULT.services


def test_word_entries_must_be_lowercase_single_token() -> None:
    with pytest.raises(ValidationError):
        Vocabulary.model_validate(
            {
                "tolerance": {"exact_max_len": 3, "swap_only_len": 4, "one_edit_min_len": 5, "two_edit_min_len": 8},
                "services": {"dining": {"words": [{"word": "Table Two", "variants": []}]}},
                "kinds": {},
                "out_of_scope": {"words": [], "phrases": []},
            }
        )


def test_unknown_top_level_key_rejected() -> None:
    with pytest.raises(ValidationError):
        Vocabulary.model_validate(
            {
                "tolerance": {"exact_max_len": 3, "swap_only_len": 4, "one_edit_min_len": 5, "two_edit_min_len": 8},
                "services": {},
                "kinds": {},
                "out_of_scope": {"words": [], "phrases": []},
                "bogus_key": 1,
            }
        )


def test_duplicate_phrase_across_services_rejected() -> None:
    with pytest.raises(ValidationError):
        Vocabulary.model_validate(
            {
                "tolerance": {"exact_max_len": 3, "swap_only_len": 4, "one_edit_min_len": 5, "two_edit_min_len": 8},
                "services": {
                    "dining": {"words": [], "phrases": ["table for"]},
                    "stay": {"words": [], "phrases": ["table for"]},
                },
                "kinds": {},
                "out_of_scope": {"words": [], "phrases": []},
            }
        )


def test_collision_check_rejects_conflicting_fixture() -> None:
    """Two services with words within each other's tolerance band must be
    rejected at load time (research R-05)."""
    with pytest.raises(VocabularyCollisionError):
        load_vocabulary(
            data={
                "tolerance": {"exact_max_len": 3, "swap_only_len": 4, "one_edit_min_len": 5, "two_edit_min_len": 8},
                "services": {
                    "dining": {"words": [{"word": "table", "variants": []}]},
                    "stay": {"words": [{"word": "tabld", "variants": []}]},  # 1 edit from "table"
                },
                "kinds": {},
                "out_of_scope": {"words": [], "phrases": []},
            }
        )
