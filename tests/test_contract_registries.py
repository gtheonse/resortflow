"""T030 — the node-name registry and the telemetry attribute vocabulary,
asserted literally.

Closes two gaps the /speckit-analyze pass found: FR-024/FR-021/SC-012 had
no frozen-set test for `graph/nodes.py` (the state-key pattern in
`test_state_contract.py` was never extended to nodes), and FR-022/SC-010
had no content test for `observability/attributes.py`. Depends on T007,
T008.

Node count note: this file (and tasks.md's own prose) says "21-entry"; the
authoritative source, `data-model.md`'s own "Graph Node Name Registry"
table, lists 22 names — it explicitly requires both reserved names
(`offer_agent`, `human_handoff`) to be present, which only totals 22.
`graph/nodes.py` was built to match the table (22), and this test asserts
the actual, authoritative count rather than the "21" in tasks.md's prose.

**Pre-existing gap found and closed (feature 010)**: this frozen set had
already drifted from `graph/nodes.py` before this feature touched
anything — `clarify` (ADR-024) and `knowledge_qa` (ADR-038) were both
added to `graph/nodes.py`/`ALL_NODES` by features 005/006 but never added
here, so this test was silently failing (confirmed by running it against
the pre-010 tree). Feature 010 closes that gap in the same edit that adds
its own four names (`booking_lookup`, `modify_request`, `decline`,
`smalltalk`, ADR-055) — 22 (the original, now-corrected count) + 2
(the pre-existing gap) + 4 (this feature) = 28.

**Feature 011** adds `browse` (ADR-059) — 29.
"""

from __future__ import annotations

from graph.nodes import ALL_NODES
from observability.attributes import (
    ALL_NAMESPACES,
    BOUNDED_METRIC_DIMENSIONS,
    GEN_AI_NAMESPACE,
    IDEMPOTENCY_KEY,
    LANGFUSE_NAMESPACE,
    RESORTFLOW_NAMESPACE,
    SAGA_ID,
    SESSION_ID,
    SPAN_ID,
    TRACE_ID,
    UNBOUNDED_IDENTIFIERS,
)

EXPECTED_NODES = frozenset(
    {
        "intent_router",
        "clarify",
        "knowledge_qa",
        "deterministic",
        "supervisor",
        "policy_preflight",
        "blocked",
        "planner",
        "dining",
        "entertainment",
        "stay",
        "wellness",
        "gaming",
        "conflict_check",
        "proposal_assembly",
        "human_approval",
        "commit",
        "release",
        "commit_verify",
        "recovery",
        "policy_postflight",
        "respond",
        "offer_agent",
        "human_handoff",
        "booking_lookup",
        "modify_request",
        "decline",
        "smalltalk",
        "browse",
    }
)


def test_node_registry_matches_data_model_literally() -> None:
    assert len(EXPECTED_NODES) == 29
    assert ALL_NODES == EXPECTED_NODES


def test_reserved_unresolved_nodes_are_present() -> None:
    """data-model.md's "Unresolved source inconsistency" note: both
    `offer_agent` and `human_handoff` are reserved even though the source
    diagram doesn't draw either as a distinct state."""
    assert "offer_agent" in ALL_NODES
    assert "human_handoff" in ALL_NODES


# --- Telemetry attribute vocabulary (contracts/telemetry-attributes.md) -----


def test_five_identifiers_present() -> None:
    assert {SESSION_ID, TRACE_ID, SPAN_ID, SAGA_ID, IDEMPOTENCY_KEY} == {
        "session_id",
        "trace_id",
        "span_id",
        "saga_id",
        "idempotency_key",
    }


def test_three_namespace_prefixes_present() -> None:
    assert ALL_NAMESPACES == {"gen_ai", "langfuse", "resortflow"}
    assert GEN_AI_NAMESPACE == "gen_ai"
    assert LANGFUSE_NAMESPACE == "langfuse"
    assert RESORTFLOW_NAMESPACE == "resortflow"


def test_cardinality_rules_partition_correctly() -> None:
    """`session_id`/`saga_id` are unbounded (span attributes only);
    `guest_id` is deliberately absent — it is a state field, not a
    telemetry identifier this module defines. `kind`/`tier`/`tier2_outcome`
    added feature 010 (routing decision, bounded enumerations — see
    `specs/001-contracts-foundation/contracts/telemetry-attributes.md`'s
    "Amendment — Feature 010")."""
    assert UNBOUNDED_IDENTIFIERS == {"session_id", "saga_id"}
    assert BOUNDED_METRIC_DIMENSIONS == {
        "tenant_id", "node", "domain", "model", "outcome", "kind", "tier", "tier2_outcome",
    }
    assert UNBOUNDED_IDENTIFIERS.isdisjoint(BOUNDED_METRIC_DIMENSIONS)
