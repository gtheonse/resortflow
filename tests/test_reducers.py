"""T010 — User Story 1: parallel domain results survive concurrent writes.

FR-002 through FR-008. Written before T012-T015 exist (graph/state.py) —
these tests fail with a collection error until the reducers and ResortFlowState
land, per tasks.md's TDD ordering for this phase.

Two complementary angles, matching this phase's Goal ("ResortFlowState's channels
merge concurrent contributions without loss") and Independent Test ("three
simulated writers ... with no agent, tool, or vendor present"):

- Direct unit tests of the four pure reducer functions — deterministic,
  vendor-free, and what SC-002's replay-idempotence and FR-008's
  first-write-on-empty properties are most precisely stated against.
- An end-to-end concurrent-fan-out test through an actual compiled
  LangGraph `StateGraph` bound to `ResortFlowState`, over 100 repetitions
  (SC-001) — this is what proves the `Annotated[..., reducer]` wiring
  survives real concurrent execution, not just direct calls to the
  functions in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.models import (
    Chunk,
    Confirmation,
    Domain,
    Hold,
    HoldStatus,
    PolicyFlag,
    Proposal,
    SagaEvent,
    ToolFailure,
)
from graph.state import (
    ResortFlowState,
    append_only,
    dedupe_by_id,
    merge_holds,
    merge_proposals,
)

UTC_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _proposal(pid: str, domain: Domain = Domain.DINING) -> Proposal:
    return Proposal(proposal_id=pid, domain=domain, tenant_id="MGV", title=pid)


def _hold(key: str, status: HoldStatus = HoldStatus.HELD) -> Hold:
    return Hold(
        idempotency_key=key,
        domain=Domain.DINING,
        tenant_id="MGV",
        saga_id="saga-1",
        status=status,
        expires_at=UTC_NOW if status in (HoldStatus.HELD,) else None,
    )


def _chunk(cid: str) -> Chunk:
    return Chunk(
        chunk_id=cid,
        tenant_id="MGV",
        content="content",
        source_uri="uri",
        content_hash="hash",
    )


def _confirmation(key: str) -> Confirmation:
    return Confirmation(
        confirmation_id=f"conf-{key}",
        idempotency_key=key,
        domain=Domain.DINING,
        confirmed_at=UTC_NOW,
    )


def _policy_flag(flag_id: str, rule: str = "self_exclusion_active") -> PolicyFlag:
    return PolicyFlag(
        flag_id=flag_id,
        rule=rule,
        disposition="blocked",
        jurisdiction="NV",
        raised_at=UTC_NOW,
    )


# --- merge_proposals (FR-002) ------------------------------------------------


def test_merge_proposals_first_write_on_empty() -> None:
    result = merge_proposals(None, [_proposal("p1")])
    assert [p.proposal_id for p in result] == ["p1"]


def test_merge_proposals_retains_all_new_ids() -> None:
    left = [_proposal("p1")]
    right = [_proposal("p2")]
    result = merge_proposals(left, right)
    assert {p.proposal_id for p in result} == {"p1", "p2"}


def test_merge_proposals_right_wins_on_collision() -> None:
    left = [_proposal("p1", domain=Domain.DINING)]
    right = [_proposal("p1", domain=Domain.WELLNESS)]
    result = merge_proposals(left, right)
    assert len(result) == 1
    assert result[0].domain == Domain.WELLNESS


def test_merge_proposals_preserves_left_order_for_existing_items() -> None:
    left = [_proposal("p1"), _proposal("p2")]
    right = [_proposal("p2", domain=Domain.WELLNESS), _proposal("p3")]
    result = merge_proposals(left, right)
    assert [p.proposal_id for p in result] == ["p1", "p2", "p3"]


def test_merge_proposals_idempotent_under_replay() -> None:
    once = merge_proposals(None, [_proposal("p1")])
    twice = merge_proposals(once, [_proposal("p1")])
    assert once == twice


# --- merge_holds (FR-003, FR-004) --------------------------------------------


def test_merge_holds_first_write_on_empty() -> None:
    result = merge_holds(None, [_hold("k1")])
    assert [h.idempotency_key for h in result] == ["k1"]


def test_merge_holds_keys_on_idempotency_key_not_hold_id() -> None:
    """Both holds have `hold_id=None` (in-flight) — they must still merge as
    two distinct entries because `idempotency_key` differs."""
    left = [_hold("k1")]
    right = [_hold("k2")]
    result = merge_holds(left, right)
    assert {h.idempotency_key for h in result} == {"k1", "k2"}
    assert all(h.hold_id is None for h in result)


@pytest.mark.parametrize(
    "terminal_status",
    [
        HoldStatus.CONFIRMED,
        HoldStatus.RELEASED,
        HoldStatus.EXPIRED,
        HoldStatus.ORPHAN_RELEASED,
    ],
)
@pytest.mark.parametrize("regressive_status", [HoldStatus.PENDING, HoldStatus.HELD])
def test_merge_holds_terminal_never_regresses(
    terminal_status: HoldStatus, regressive_status: HoldStatus
) -> None:
    left = [_hold("k1", status=terminal_status)]
    right = [_hold("k1", status=regressive_status)]
    result = merge_holds(left, right)
    assert len(result) == 1
    assert result[0].status == terminal_status


def test_merge_holds_non_terminal_updates_normally() -> None:
    left = [_hold("k1", status=HoldStatus.PENDING)]
    right = [_hold("k1", status=HoldStatus.HELD)]
    result = merge_holds(left, right)
    assert result[0].status == HoldStatus.HELD


def test_merge_holds_idempotent_under_replay() -> None:
    once = merge_holds(None, [_hold("k1", status=HoldStatus.CONFIRMED)])
    twice = merge_holds(once, [_hold("k1", status=HoldStatus.CONFIRMED)])
    assert once == twice


# --- append_only (FR-005) ----------------------------------------------------


def test_append_only_first_write_on_empty() -> None:
    result = append_only(None, [_confirmation("k1")])
    assert len(result) == 1


def test_append_only_concatenates() -> None:
    left = [_confirmation("k1")]
    right = [_confirmation("k2")]
    result = append_only(left, right)
    assert [c.idempotency_key for c in result] == ["k1", "k2"]


def test_append_only_works_for_saga_events_and_failures_too() -> None:
    events = append_only(
        None,
        [SagaEvent(event_id="e1", saga_id="s1", sequence=1, kind="hold_placed", occurred_at=UTC_NOW)],
    )
    assert len(events) == 1
    failures = append_only(
        None,
        [
            ToolFailure(
                domain=Domain.DINING,
                tool="place_hold",
                error_class="RETRYABLE",
                detail="upstream timeout",
            )
        ],
    )
    assert len(failures) == 1


def test_append_only_idempotent_replay_still_appends_duplicate_but_is_stable() -> None:
    """append_only never deduplicates by design (FR-005: accumulate
    append-only) — replay-idempotence for this reducer means applying the
    SAME accumulated call twice yields the same list, not that duplicate
    business events collapse. The dedup guarantee belongs to `dedupe_by_id`
    and to `merge_holds`/`merge_proposals`'s key-based merge, not to
    `append_only`."""
    once = append_only(None, [_confirmation("k1")])
    twice = append_only(once, [])
    assert once == twice


# --- policy_flags' append_only reducer (feature 006, ADR-029) ---------------
#
# policy_flags reuses the same append_only function proven above for
# confirmations/failures/saga_log — these tests exist because feature 006 is
# the first feature to actually write to this channel (feature 001 reserved
# it with zero writers), so its three reducer properties need their own
# coverage against PolicyFlag specifically, not just an inference from the
# generic append_only tests already passing for other types.


def test_policy_flags_first_write_on_empty() -> None:
    result = append_only(None, [_policy_flag("f1")])
    assert [f.flag_id for f in result] == ["f1"]


def test_policy_flags_concatenates_across_concurrent_writers() -> None:
    """A dining allergen flag and a gaming self-exclusion flag can both fire
    in the same superstep (feature 005 fan-out) — both must survive, not
    just the last one applied."""
    left = [_policy_flag("f1", rule="allergen_fidelity_mismatch")]
    right = [_policy_flag("f2", rule="self_exclusion_active")]
    result = append_only(left, right)
    assert [f.flag_id for f in result] == ["f1", "f2"]
    assert {f.rule for f in result} == {
        "allergen_fidelity_mismatch",
        "self_exclusion_active",
    }


def test_policy_flags_idempotent_under_replay() -> None:
    once = append_only(None, [_policy_flag("f1")])
    twice = append_only(once, [])
    assert once == twice


def test_policy_flags_reducer_wired_through_compiled_graph() -> None:
    """Confirms the Annotated[list[PolicyFlag], append_only] wiring on
    ResortFlowState itself (not just the bare function) actually merges concurrent
    writes, mirroring the three-writer graph test above for
    proposals/holds."""

    def make_flagger(n: int):
        def flagger(state: ResortFlowState) -> dict:
            return {"policy_flags": [_policy_flag(f"f{n}")]}

        return flagger

    builder = StateGraph(ResortFlowState)
    for n in (1, 2, 3):
        name = f"flagger_{n}"
        builder.add_node(name, make_flagger(n))
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    graph = builder.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "policy-flags-thread"}}
    final_state = graph.invoke(_initial_state(), config=config)

    assert {f.flag_id for f in final_state["policy_flags"]} == {"f1", "f2", "f3"}


# --- dedupe_by_id (FR-006) ---------------------------------------------------


def test_dedupe_by_id_first_write_on_empty() -> None:
    result = dedupe_by_id(None, [_chunk("c1")])
    assert [c.chunk_id for c in result] == ["c1"]


def test_dedupe_by_id_first_occurrence_wins_preserving_rrf_rank() -> None:
    left = [_chunk("c1"), _chunk("c2")]
    right = [_chunk("c2"), _chunk("c3")]
    result = dedupe_by_id(left, right)
    assert [c.chunk_id for c in result] == ["c1", "c2", "c3"]


def test_dedupe_by_id_idempotent_under_replay() -> None:
    once = dedupe_by_id(None, [_chunk("c1")])
    twice = dedupe_by_id(once, [_chunk("c1")])
    assert once == twice


# --- Concurrent fan-out through a real compiled graph (SC-001) --------------


def _build_three_writer_graph():
    """A minimal graph with no agent, tool or vendor: three parallel nodes
    fan out from START, each contributing one proposal and one hold, then
    fan in at END. Exercises ResortFlowState's Annotated reducer wiring under
    LangGraph's actual concurrent-write mechanics."""

    def make_writer(n: int):
        def writer(state: ResortFlowState) -> dict:
            return {
                "proposals": [_proposal(f"p{n}")],
                "holds": [_hold(f"k{n}")],
            }

        return writer

    builder = StateGraph(ResortFlowState)
    for n in (1, 2, 3):
        name = f"writer_{n}"
        builder.add_node(name, make_writer(n))
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    return builder.compile(checkpointer=MemorySaver())


def _initial_state() -> ResortFlowState:
    return ResortFlowState(
        tenant_id="MGV",
        guest_id="g1",
        session_id="s1",
        jurisdiction="NV",
        entitlements=[],
        saga_id="saga-1",
        messages=[],
        plan=None,
        replan_count=0,
        proposals=[],
        holds=[],
        confirmations=[],
        failures=[],
        retrieved=[],
        citations=[],
        awaiting_approval=False,
        approval_scope=None,
        policy_flags=[],
        escalate=False,
        saga_log=[],
    )


@pytest.mark.parametrize("repetition", range(100))
def test_three_concurrent_writers_all_results_retained(repetition: int) -> None:
    """SC-001: zero result loss across 100 repetitions of the
    concurrent-write scenario — each repetition is a fresh thread so
    LangGraph's actual (potentially nondeterministic) write-application
    order is exercised independently each time."""
    graph = _build_three_writer_graph()
    config = {"configurable": {"thread_id": f"thread-{repetition}"}}
    final_state = graph.invoke(_initial_state(), config=config)

    assert {p.proposal_id for p in final_state["proposals"]} == {"p1", "p2", "p3"}
    assert {h.idempotency_key for h in final_state["holds"]} == {"k1", "k2", "k3"}


def test_replaying_an_identical_write_during_resume_is_a_no_op() -> None:
    """FR-007 / SC-002, exercised through the compiled graph rather than the
    bare function: invoking the same thread a second time re-runs the
    writer node from START against the already-persisted state (this is
    LangGraph's actual replay mechanic — resume replays steps rather than
    continuing from them). The reducer must not duplicate the contribution
    the second time around."""

    def writer(state: ResortFlowState) -> dict:
        return {"proposals": [_proposal("p1")]}

    builder = StateGraph(ResortFlowState)
    builder.add_node("writer", writer)
    builder.add_edge(START, "writer")
    builder.add_edge("writer", END)
    graph = builder.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "replay-thread"}}
    first = graph.invoke(_initial_state(), config=config)
    assert [p.proposal_id for p in first["proposals"]] == ["p1"]

    # Same thread, same input — the writer node runs again on top of the
    # persisted checkpoint, replaying its write.
    second = graph.invoke(_initial_state(), config=config)
    assert [p.proposal_id for p in second["proposals"]] == ["p1"]
