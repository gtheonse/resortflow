"""T054 -- structural checks on `eval/data/intent_cases.yaml`, the ground truth for
`intent_extraction_accuracy`/`dietary_recall` (research R-08). This is the labelled dataset, not the
scoring logic (see `test_intent_scorer.py`) -- what's being guarded here is that the fixture itself
still satisfies the coverage rule the metric floor depends on: every dietary vocabulary value is
exercised by at least one case, so a prompt that silently drops one restriction or severity from its
vocabulary has a chance of being caught."""

from __future__ import annotations

from pathlib import Path

import yaml

from graph.models import DietaryRestriction, DietarySeverity

_PATH = Path(__file__).parent.parent / "data" / "intent_cases.yaml"


def _load() -> dict:
    return yaml.safe_load(_PATH.read_text(encoding="utf-8"))


def test_at_least_twelve_cases_per_tenant() -> None:
    data = _load()
    by_tenant: dict[str, int] = {}
    for case in data["cases"]:
        by_tenant[case["tenant_id"]] = by_tenant.get(case["tenant_id"], 0) + 1
    assert by_tenant.get("MGV", 0) >= 12
    assert by_tenant.get("MHB", 0) >= 12


def test_at_least_six_dietary_cases_per_tenant() -> None:
    data = _load()
    by_tenant: dict[str, int] = {}
    for case in data["cases"]:
        if case["expected"].get("dietary"):
            by_tenant[case["tenant_id"]] = by_tenant.get(case["tenant_id"], 0) + 1
    assert by_tenant.get("MGV", 0) >= 6
    assert by_tenant.get("MHB", 0) >= 6


def test_every_dietary_restriction_and_severity_value_appears_at_least_once() -> None:
    data = _load()
    restrictions: set[str] = set()
    severities: set[str] = set()
    for case in data["cases"]:
        for entry in case["expected"].get("dietary", []):
            restrictions.add(entry["restriction"])
            severities.add(entry["severity"])
    assert restrictions == {r.value for r in DietaryRestriction}
    assert severities == {s.value for s in DietarySeverity}


def test_a_fixed_today_anchors_relative_dates() -> None:
    data = _load()
    assert str(data["today"]) == "2026-01-15"


def test_every_case_id_is_unique() -> None:
    data = _load()
    ids = [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids))
