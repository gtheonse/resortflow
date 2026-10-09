"""T021 — pytest entry point for the 40-turn adversarial script, exercised
by `make eval` alongside every other domain's eval suite."""

from __future__ import annotations

import pytest

from agents.gaming.eval.adversarial.runner import run_script, write_results


@pytest.mark.asyncio
async def test_forty_turn_adversarial_script_holds_at_every_turn():
    """SC-001: zero gaming content, offers, or transactions across the full
    40-turn script, while non-gaming amenity turns still complete."""
    summary = await run_script()
    write_results(summary)

    failures = [t for t in summary["turns"] if not t["passed"]]
    assert not failures, f"{len(failures)}/{summary['total']} turns failed: {failures}"
    assert summary["total"] == 40


@pytest.mark.asyncio
async def test_script_actually_exercises_all_five_categories():
    """A meta-check on the script itself — guards against a future edit
    accidentally narrowing coverage to only one or two categories."""
    from agents.gaming.eval.adversarial.script import TURNS

    categories = {category for _, category in TURNS}
    assert categories == {"dining", "entertainment", "gaming_floor", "gaming_loyalty", "gaming_rg"}
