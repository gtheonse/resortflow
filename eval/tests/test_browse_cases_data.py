"""Feature 011, T002 — structural checks on the browse and near-miss cases in
`eval/data/routing_cases.yaml` (`contracts/browse-eval-gate.md`). Guards the
ground truth itself, authored **before** the `kinds.browse` vocabulary and
independently of it (feature 010's D1 lesson: a vocabulary-fitted case set
makes the gate decorative).

Must fail before T003 (the cases' content)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

_PATH = Path(__file__).parent.parent / "data" / "routing_cases.yaml"

BROWSE_CATEGORIES = frozenset(
    {
        "browse_dining", "browse_events", "browse_stay_period", "browse_overview", "browse_wellness",
        "browse_rooms", "browse_gaming", "browse_refinement",
        "near_miss_faq", "near_miss_book", "near_miss_lookup", "stay_length",
    }
)

# Verbatim phrases reported live (spec.md Context) — each must appear with its label.
_REPORTED = {
    "what are the dining venues available in nevada": ("browse", ["dining"]),
    "list the resort experiences": ("browse", []),
    "what all events are happening in my stay period": ("browse", ["entertainment"]),
    "what events are happening in the next 5 days": ("browse", ["entertainment"]),
    "book stay for next 5 days": ("book", ["stay"]),
}


def _cases() -> list[dict]:
    return [c for c in yaml.safe_load(_PATH.read_text(encoding="utf-8"))["cases"] if c["category"] in BROWSE_CATEGORIES]


def test_every_browse_category_is_present_for_both_tenants() -> None:
    counts: Counter[tuple[str, str]] = Counter((c["category"], c["tenant_id"]) for c in _cases())
    for category in BROWSE_CATEGORIES:
        for tenant in ("MGV", "MHB"):
            assert counts[(category, tenant)] >= 4, f"{category}/{tenant} has only {counts[(category, tenant)]}"


def test_holdout_has_at_least_two_per_category_per_tenant() -> None:
    counts: Counter[tuple[str, str]] = Counter((c["category"], c["tenant_id"]) for c in _cases() if c["split"] == "holdout")
    for category in BROWSE_CATEGORIES:
        for tenant in ("MGV", "MHB"):
            assert counts[(category, tenant)] >= 2, f"{category}/{tenant} holdout has only {counts[(category, tenant)]}"


def test_every_case_has_a_valid_split_and_unique_id() -> None:
    cases = _cases()
    assert all(c["split"] in ("dev", "holdout") for c in cases)
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_browse_categories_expect_browse_and_near_misses_never_do() -> None:
    for c in _cases():
        if c["category"].startswith("browse_"):
            assert c["expected_kind"] == "browse", c["id"]
        elif c["category"].startswith("near_miss_"):
            assert c["expected_kind"] != "browse", c["id"]
    assert {c["expected_kind"] for c in _cases() if c["category"] == "near_miss_faq"} == {"faq"}
    assert {c["expected_kind"] for c in _cases() if c["category"] == "near_miss_book"} == {"book"}
    assert {c["expected_kind"] for c in _cases() if c["category"] == "near_miss_lookup"} == {"lookup"}
    assert {c["expected_kind"] for c in _cases() if c["category"] == "stay_length"} == {"book"}


def test_every_reported_phrase_appears_verbatim_with_its_label() -> None:
    by_text: dict[str, list[dict]] = {}
    for c in _cases():
        by_text.setdefault(c["text"], []).append(c)
    for text, (kind, services) in _REPORTED.items():
        assert text in by_text, f"missing reported phrase: {text!r}"
        for c in by_text[text]:
            assert c["expected_kind"] == kind and sorted(c["expected_services"]) == sorted(services), c["id"]


def test_refinement_cases_carry_a_previous_browse() -> None:
    refinements = [c for c in _cases() if c["category"] == "browse_refinement"]
    assert refinements
    for c in refinements:
        assert c["previous"]["kind"] == "browse", c["id"]
        assert {"kind", "services", "had_booking"} <= set(c["previous"]), c["id"]


def test_no_case_is_a_duplicate_of_an_existing_feature_010_case_with_a_different_label() -> None:
    all_cases = yaml.safe_load(_PATH.read_text(encoding="utf-8"))["cases"]
    seen: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
    for c in all_cases:
        key = (c["tenant_id"], c["text"])
        value = (c["expected_kind"], tuple(sorted(c["expected_services"])))
        if key in seen:
            assert seen[key] == value, f"conflicting labels for {key}"
        seen[key] = value
