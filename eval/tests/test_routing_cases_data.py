"""Feature 010, T028 — structural checks on `eval/data/routing_cases.yaml`
(research R-13). This guards the ground truth itself, authored **before**
`graph/routing/vocabulary.yaml` and independently of it (analyze finding
D1: a vocabulary-fitted case set makes the gate decorative) — not the
scoring logic (`eval/routing_scorer.py`).

Must fail before T029 (the case file's content)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

_PATH = Path(__file__).parent.parent / "data" / "routing_cases.yaml"

_CATEGORIES = frozenset(
    {
        "typo", "variant", "substring_trap", "multi_service", "lookup", "modify",
        "out_of_scope", "smalltalk", "faq", "followup", "mixed",
    }
)

# Verbatim spec.md examples every one of which must appear somewhere in the set.
_SPEC_EXAMPLES = (
    "book a restaurent table for 4 at 8pm",
    "can I get tickets for the theater tonight",
    "the shower in my room is broken",
    "dinner and a show tomorrow",
    "I'd like a masage at the spa",
    "what is the restaurent i got reserved for sep 30, 8 PM?",
    "book flight for me to fly to India",
    "cancel my dinner reservation",
    "move my spa appointment to 4pm",
    "tabel for 2 tonight",
    "cancle my dinner",
    "can I zoom in on the room photos",
    "is it confirmed?",
    "same again for tomorrow",
    "make it 6 people",
)


def _load() -> dict:
    return yaml.safe_load(_PATH.read_text(encoding="utf-8"))


# Feature 011 adds its own categories to the same file; their structure is guarded by
# eval/tests/test_browse_cases_data.py, so this feature-010 test only needs to recognise them.
_BROWSE_CATEGORIES = frozenset(
    {
        "browse_dining", "browse_events", "browse_stay_period", "browse_overview", "browse_wellness",
        "browse_rooms", "browse_gaming", "browse_refinement",
        "near_miss_faq", "near_miss_book", "near_miss_lookup", "stay_length",
    }
)


def test_every_case_has_a_valid_category_and_split() -> None:
    data = _load()
    for case in data["cases"]:
        assert case["category"] in (_CATEGORIES | _BROWSE_CATEGORIES), case["id"]
        assert case["split"] in ("dev", "holdout"), case["id"]


def test_at_least_eight_cases_per_category_per_tenant() -> None:
    data = _load()
    counts: Counter[tuple[str, str]] = Counter()
    for case in data["cases"]:
        counts[(case["category"], case["tenant_id"])] += 1
    for category in _CATEGORIES:
        for tenant in ("MGV", "MHB"):
            assert counts[(category, tenant)] >= 8, f"{category}/{tenant} has only {counts[(category, tenant)]}"


def test_holdout_is_25_to_35_percent_overall() -> None:
    data = _load()
    total = len(data["cases"])
    holdout = sum(1 for c in data["cases"] if c["split"] == "holdout")
    fraction = holdout / total
    assert 0.25 <= fraction <= 0.35, f"holdout fraction is {fraction:.2f}"


def test_holdout_has_at_least_two_per_category_per_tenant() -> None:
    data = _load()
    counts: Counter[tuple[str, str]] = Counter()
    for case in data["cases"]:
        if case["split"] == "holdout":
            counts[(case["category"], case["tenant_id"])] += 1
    for category in _CATEGORIES:
        for tenant in ("MGV", "MHB"):
            assert counts[(category, tenant)] >= 2, f"{category}/{tenant} holdout has only {counts[(category, tenant)]}"


def test_both_tenants_carry_out_of_scope_and_lookup_cases() -> None:
    data = _load()
    by_category_tenant: set[tuple[str, str]] = set()
    for case in data["cases"]:
        by_category_tenant.add((case["category"], case["tenant_id"]))
    for tenant in ("MGV", "MHB"):
        assert ("out_of_scope", tenant) in by_category_tenant
        assert ("lookup", tenant) in by_category_tenant


def test_every_spec_named_example_is_present_verbatim() -> None:
    data = _load()
    texts = {case["text"] for case in data["cases"]}
    for example in _SPEC_EXAMPLES:
        assert example in texts, f"missing verbatim spec example: {example!r}"


def test_every_case_id_is_unique() -> None:
    data = _load()
    ids = [case["id"] for case in data["cases"]]
    assert len(ids) == len(set(ids))


def test_followup_cases_carry_previous_turn_context() -> None:
    data = _load()
    followups = [c for c in data["cases"] if c["category"] == "followup"]
    assert followups
    for case in followups:
        assert "previous" in case, case["id"]
        assert {"kind", "services", "had_booking"} <= set(case["previous"])


def test_at_least_176_cases_total() -> None:
    data = _load()
    assert len(data["cases"]) >= 176
