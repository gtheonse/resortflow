"""T011 — the frozen `ResortFlowState` key set and `MAX_REPLAN_COUNT`.

FR-021, FR-012, SC-012. Asserts the key set literally, per data-model.md's
"`ResortFlowState` — the frozen key set", so a later feature editing the schema
fails CI rather than drifting silently. Written before T012-T015 exist —
fails with a collection error until `graph/state.py` lands.

**Pre-existing gap found and closed (feature 010)**: this test still said
20 and omitted feature 005's own additive amendment (`graph_version`,
`clarification_count`, `escalate_reason` — `contracts/state-schema-amendment-005.md`)
— confirmed by running it against the pre-010 tree, where it already failed.
Feature 010 closes that gap in the same edit that adds its own two keys
(`routing`, `turn_baseline` — ADR-056): 20 + 3 (005) + 2 (010) = 25.
"""

from __future__ import annotations

from graph.state import MAX_REPLAN_COUNT, ResortFlowState

FROZEN_KEYS = frozenset(
    {
        "tenant_id",
        "guest_id",
        "session_id",
        "jurisdiction",
        "entitlements",
        "saga_id",
        "messages",
        "plan",
        "replan_count",
        "proposals",
        "holds",
        "confirmations",
        "failures",
        "retrieved",
        "citations",
        "awaiting_approval",
        "approval_scope",
        "policy_flags",
        "escalate",
        "saga_log",
        "graph_version",
        "clarification_count",
        "escalate_reason",
        "routing",
        "turn_baseline",
    }
)


def test_resortflow_state_has_exactly_twenty_five_frozen_keys() -> None:
    assert len(FROZEN_KEYS) == 25
    assert set(ResortFlowState.__annotations__.keys()) == FROZEN_KEYS


def test_max_replan_count_is_three() -> None:
    assert MAX_REPLAN_COUNT == 3
