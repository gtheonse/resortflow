"""T015 — contract-conformance tests for `eval/coverage.py`'s exemption
rules (`contracts/coverage-exemption-schema.md`): expiry-window math per
tier, a stale `active` entry past `expires_at` treated as `void` at read
time regardless of the stored value, and a 3rd request without `adr_ref`
rejected."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eval.coverage import (
    CoverageRequirement,
    clear_exemption_if_covered,
    evaluate_coverage,
    new_exemption,
)


def test_expiry_window_matches_urgency_tier():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    tier_1 = new_exemption(
        surface_id="tool.hold_table", urgency_tier="tier_1",
        reason="r", tracking_issue="#1", now=now,
    )
    tier_2 = new_exemption(
        surface_id="policy.dietary", urgency_tier="tier_2",
        reason="r", tracking_issue="#1", now=now,
    )
    other = new_exemption(
        surface_id="misc.thing", urgency_tier="other",
        reason="r", tracking_issue="#1", now=now,
    )
    assert tier_1.expires_at == now + timedelta(days=7)
    assert tier_2.expires_at == now + timedelta(days=14)
    assert other.expires_at == now + timedelta(days=30)


def test_stale_active_entry_past_expiry_is_void_at_read_time():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    exemption = new_exemption(
        surface_id="tool.hold_table", urgency_tier="tier_1",
        reason="r", tracking_issue="#1", now=now,
    )
    assert exemption.status == "active"  # the stored value never changes itself
    ten_days_later = now + timedelta(days=10)
    assert exemption.effective_status(now=ten_days_later) == "void"


def test_third_request_without_adr_ref_is_rejected():
    with pytest.raises(ValueError, match="FR-029"):
        new_exemption(
            surface_id="tool.hold_table", urgency_tier="tier_1",
            reason="r", tracking_issue="#1", request_sequence=3,
        )


def test_third_request_with_adr_ref_succeeds():
    exemption = new_exemption(
        surface_id="tool.hold_table", urgency_tier="tier_1",
        reason="r", tracking_issue="#1", request_sequence=3, adr_ref="047",
    )
    assert exemption.adr_ref == "047"


def test_closing_tracking_issue_alone_does_not_clear_exemption():
    """FR-026: only the coverage gate actually passing clears an exemption —
    this function has no issue-tracker integration to even simulate closing
    one, which is itself the point."""
    exemption = new_exemption(
        surface_id="tool.hold_table", urgency_tier="tier_1",
        reason="r", tracking_issue="#1",
    )
    # No call to clear_exemption_if_covered — simulating "issue closed,
    # nothing else happened" — the exemption must remain active.
    assert exemption.effective_status() == "active"


def test_clear_exemption_if_covered_clears_only_on_pass():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    exemption = new_exemption(
        surface_id="tool.hold_table", urgency_tier="tier_1",
        reason="r", tracking_issue="#1", now=now,
    )
    clear_exemption_if_covered([exemption], "tool.hold_table", now=now)
    assert exemption.status == "cleared"


def test_evaluate_coverage_uses_active_exemption_to_mark_covered():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    requirement = CoverageRequirement(surface_id="tool.hold_table", urgency_tier="tier_1")
    exemption = new_exemption(
        surface_id="tool.hold_table", urgency_tier="tier_1",
        reason="r", tracking_issue="#1", now=now,
    )
    rows, active = evaluate_coverage([requirement], set(), [exemption], now=now)
    assert rows == [{"surface_id": "tool.hold_table", "urgency_tier": "tier_1", "covered": True}]
    assert active == [exemption]


def test_evaluate_coverage_void_exemption_does_not_cover():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    requirement = CoverageRequirement(surface_id="tool.hold_table", urgency_tier="tier_1")
    exemption = new_exemption(
        surface_id="tool.hold_table", urgency_tier="tier_1",
        reason="r", tracking_issue="#1", now=now,
    )
    later = now + timedelta(days=8)  # past the 7-day tier_1 window
    rows, active = evaluate_coverage([requirement], set(), [exemption], now=later)
    assert rows == [{"surface_id": "tool.hold_table", "urgency_tier": "tier_1", "covered": False}]
    assert active == []
