"""T023 -- the dietary enumerations are bound by a member snapshot (FR-037; Principle IV).

The dietary restriction and severity vocabularies are compliance policy. This test fails when the live
enumerations in `graph/models.py` differ from `prompts/snapshots/dietary_enums.json` in members OR ORDER
(a re-ranking is also a visible change). Updating the snapshot needs the compliance owner's approval
(the whole `prompts/` tree defaults to compliance under the FR-042 last-match-wins rules), so a restriction
cannot be added, removed or re-ranked on engineering review alone.

Ownership of the whole model module is deliberately NOT used: it would pull unrelated model changes through
compliance review. This test runs in the PR gate and the merge-queue gate (T038a), which is what makes
"fail CI" true.
"""

from __future__ import annotations

import json
from pathlib import Path

from graph.models import DietaryRestriction, DietarySeverity

SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshots" / "dietary_enums.json"

_HOW_TO_FIX = (
    "The dietary enumerations changed. This is compliance policy: update prompts/snapshots/dietary_enums.json "
    "in the same change and get the compliance owner's approval (FR-037)."
)


def _snapshot() -> dict[str, list[str]]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_restriction_members_and_order_match_the_snapshot() -> None:
    live = [member.value for member in DietaryRestriction]
    assert live == _snapshot()["DietaryRestriction"], _HOW_TO_FIX


def test_severity_members_and_order_match_the_snapshot() -> None:
    live = [member.value for member in DietarySeverity]
    assert live == _snapshot()["DietarySeverity"], _HOW_TO_FIX


def test_snapshot_has_exactly_the_two_enumerations() -> None:
    assert set(_snapshot()) == {"DietaryRestriction", "DietarySeverity"}
