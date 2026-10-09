"""T031/T032 — the Saga Failure Case registry (data-model.md's "Saga Failure
Case" entity) and its runner. This module deliberately does NOT reimplement
any saga-failure assertion: research.md's R9 decision is that rewriting the
already-hardened `agents/*/tests/*.py` / `agents/tests/*.py` permutations
into a second, parallel eval-only implementation would risk *reintroducing*
bugs those tests were written to catch, for zero coverage benefit. Instead
each `SagaFailureCase` is a thin pointer at an existing (or newly added,
per T032's matrix audit) pytest test, and `run_saga_suite` executes those
tests programmatically and folds the pytest outcome into a `SagaCaseResult`.

Zero-tolerance is enforced entirely by the caller: `eval/gate.py`'s
`compute_verdict` already treats any `SagaCaseResult(passed=False)` as a
hard `FAIL` (FR-007 binary gate) — this module's only job is to produce
accurate `SagaCaseResult`s, one per registered permutation, never collapsed
(SC-003).

The domain x failing_step matrix (dining/stay/entertainment x
hold/confirm/compensate/none — gaming is out of scope, it is read-only per
AGENTS.md's hard rule) is fully covered; see the `_CASES` list below for the
backing test of every one of the 12 cells.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from dataclasses import dataclass

import pytest

from eval.models import SagaCaseResult


@dataclass
class SagaFailureCase:
    case_id: str
    domain: str
    failing_step: str  # "hold" | "confirm" | "compensate" | "none"
    expected_outcome: str
    backing_test: str
    """A pytest node id, e.g.
    ``"agents/tests/test_partial_failure_recovery.py::test_dining_commit_failure_preserves_other_confirmations_and_proposes_alternative"``.
    """
    urgency_tier: str = "tier_1"


_CASES: list[SagaFailureCase] = [
    # --- dining -----------------------------------------------------------
    SagaFailureCase(
        case_id="dining.none.happy_path",
        domain="dining",
        failing_step="none",
        expected_outcome="hold placed then confirmed; success path baseline for the other three cells",
        backing_test="agents/dining/tests/test_graph.py::test_happy_path_produces_confirmation",
    ),
    SagaFailureCase(
        case_id="dining.hold.vendor_rejects_hold",
        domain="dining",
        failing_step="hold",
        expected_outcome="hold_table failure is classified TERMINAL, hold recorded RELEASED, never confirmed",
        backing_test="agents/dining/tests/test_graph.py::test_hold_placement_failure_is_classified_and_never_confirms",
    ),
    SagaFailureCase(
        case_id="dining.confirm.commit_failure_finds_alternative",
        domain="dining",
        failing_step="confirm",
        expected_outcome=(
            "confirm_reservation fails after entertainment already confirmed; entertainment's "
            "confirmation is preserved, a real dining alternative is proposed, no escalation"
        ),
        backing_test=(
            "agents/tests/test_partial_failure_recovery.py::"
            "test_dining_commit_failure_preserves_other_confirmations_and_proposes_alternative"
        ),
    ),
    SagaFailureCase(
        case_id="dining.compensate.no_alternative_found",
        domain="dining",
        failing_step="compensate",
        expected_outcome=(
            "confirm_reservation fails and Recovery's own alternative search is exhausted too; "
            "escalates with escalate_reason=compensation_failed, entertainment stays confirmed"
        ),
        backing_test=(
            "agents/tests/test_saga_compensation_exhausted.py::"
            "test_dining_compensation_exhausted_escalates_without_falsifying_confirmed_domains"
        ),
    ),
    # --- stay ---------------------------------------------------------------
    SagaFailureCase(
        case_id="stay.none.happy_path",
        domain="stay",
        failing_step="none",
        expected_outcome="hold placed then confirmed after human approval; success path baseline",
        backing_test="agents/stay/tests/test_graph.py::test_happy_path_produces_confirmation",
    ),
    SagaFailureCase(
        case_id="stay.hold.vendor_rejects_hold",
        domain="stay",
        failing_step="hold",
        expected_outcome="hold_room failure is classified TERMINAL, hold recorded RELEASED, never confirmed",
        backing_test="agents/stay/tests/test_graph.py::test_hold_placement_failure_is_classified_and_never_confirms",
    ),
    SagaFailureCase(
        case_id="stay.confirm.commit_failure_finds_alternative",
        domain="stay",
        failing_step="confirm",
        expected_outcome=(
            "confirm_room_reservation fails after dining/entertainment/wellness already confirmed; "
            "those three stay confirmed, a real alternative room is proposed, no escalation"
        ),
        backing_test=(
            "agents/tests/test_partial_failure_recovery_nondining.py::"
            "test_stay_commit_failure_preserves_other_confirmations_and_proposes_alternative"
        ),
    ),
    SagaFailureCase(
        case_id="stay.compensate.no_alternative_found",
        domain="stay",
        failing_step="compensate",
        expected_outcome=(
            "confirm_room_reservation fails and Recovery's own alternative room search is "
            "exhausted too; escalates with escalate_reason=compensation_failed, other domains "
            "stay confirmed"
        ),
        backing_test=(
            "agents/tests/test_saga_compensation_exhausted.py::"
            "test_stay_compensation_exhausted_escalates_without_falsifying_confirmed_domains"
        ),
    ),
    # --- entertainment --------------------------------------------------------
    SagaFailureCase(
        case_id="entertainment.none.happy_path",
        domain="entertainment",
        failing_step="none",
        expected_outcome="hold placed then purchased after human approval; success path baseline",
        backing_test="agents/entertainment/tests/test_graph.py::test_happy_path_approve_produces_confirmation",
    ),
    SagaFailureCase(
        case_id="entertainment.hold.vendor_rejects_hold",
        domain="entertainment",
        failing_step="hold",
        expected_outcome="hold_seats failure is classified TERMINAL, hold recorded RELEASED, never purchased",
        backing_test=(
            "agents/entertainment/tests/test_graph.py::"
            "test_hold_placement_failure_is_classified_and_never_purchases"
        ),
    ),
    SagaFailureCase(
        case_id="entertainment.confirm.purchase_rejected",
        domain="entertainment",
        failing_step="confirm",
        expected_outcome="purchase_tickets failure is classified and escalated; hold never silently confirmed",
        backing_test=(
            "agents/entertainment/tests/test_graph.py::"
            "test_purchase_failure_is_classified_and_hold_expires_without_confirming"
        ),
    ),
    SagaFailureCase(
        case_id="entertainment.compensate.vendor_disagrees_post_commit",
        domain="entertainment",
        failing_step="compensate",
        expected_outcome=(
            "CommitVerify catches get_order_status disagreeing with a recorded Confirmation and "
            "escalates with escalate_reason=compensation_failed, never falsifying the append-only "
            "confirmations channel (ADR-027's dead-letter path)"
        ),
        backing_test=(
            "agents/tests/test_commit_verification.py::"
            "test_commit_verify_escalates_when_the_vendor_disagrees_with_commit"
        ),
    ),
]

_CASES_BY_ID: dict[str, SagaFailureCase] = {case.case_id: case for case in _CASES}


def registered_cases() -> list[SagaFailureCase]:
    """Read-only view of the full registry — used by
    `eval/tests/test_saga_suite.py`'s completeness check and by anything
    that needs the matrix without running it."""
    return list(_CASES)


def _run_one(backing_test: str) -> bool:
    """Runs a single pytest node id in-process via `pytest.main` and
    reports whether it passed. `pytest.main` is the simplest correct way to
    reuse each domain's real fixtures (async event loop, `MemorySaver`
    checkpointers, `Fake*Tools` doubles) without hand-rolling fixture
    injection for a dozen differently-shaped test functions (per this
    module's own docstring and research.md R9) — the eval suite's job is to
    prove these tests still pass, not to re-run them faster.

    A test that errors for reasons unrelated to saga correctness (e.g. this
    sandbox's documented `torch` DLL-block on any test that reaches
    `policy_preflight`) surfaces here as `passed=False` exactly like a real
    regression would — `run_saga_suite` cannot distinguish "the saga
    behavior regressed" from "this environment can't execute the backing
    test at all", which is the correct conservative default for a merge
    gate (an unrunnable proof is not a proof)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = pytest.main(["-q", backing_test])
    return exit_code == pytest.ExitCode.OK


def run_saga_suite(domains: list[str] | None = None) -> list[SagaCaseResult]:
    """Runs every registered `SagaFailureCase` matching `domains` (all three
    transactional domains — dining/stay/entertainment — when `domains` is
    `None`; gaming is never included, it is read-only per AGENTS.md) and
    returns one `SagaCaseResult` per case, never collapsed (SC-003).

    Signature is a published interface: `eval/runner.py`'s merge-queue-tier
    wiring imports this directly, so `domains: list[str] | None = None ->
    list[SagaCaseResult]` must not change without coordinating that call
    site."""
    selected = _CASES if domains is None else [c for c in _CASES if c.domain in domains]
    results: list[SagaCaseResult] = []
    for case in selected:
        passed = _run_one(case.backing_test)
        results.append(
            SagaCaseResult(
                case_id=case.case_id,
                passed=passed,
                domain=case.domain,
                failing_step=case.failing_step,
            )
        )
    return results
