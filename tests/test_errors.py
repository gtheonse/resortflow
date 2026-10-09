"""T018 — User Story 3: every failure classifies into exactly one disposition.

FR-013, FR-014, SC-003. Written before T019's `classify()` body exists —
these tests fail until it lands, per tasks.md's TDD ordering.

Coverage is the union of the two authoritative sources FR-014 names:

1. The failure-code enumerations published by dining (7 codes) and
   ticketing (7 codes) — data-model.md's "Error taxonomy mapping", verified
   against `dining-sim.openapi.yaml` / `ticketing-sim.openapi.yaml`
   directly.
2. The nine system/durability failure modes in
   `docs/ResortFlow-HITL-Durability-State.md` §11.

`classify()`'s signature (`vendor_code, http_status, retryable_hint`) is
about *vendor* failures — the five specifications' failure shapes. System
failure modes are a different axis entirely (internal execution/durability
events, never a vendor HTTP response) and are covered separately via
`graph.errors.SYSTEM_FAILURE_DISPOSITIONS`, not by feeding synthetic codes
through `classify()`.
"""

from __future__ import annotations

import pytest

from graph.errors import SYSTEM_FAILURE_DISPOSITIONS, ErrorClass, SystemFailureMode, classify

# --- Published vendor codes (data-model.md's "Error taxonomy mapping") -----
# Verified against dining-sim.openapi.yaml / ticketing-sim.openapi.yaml's
# own `Error.code` / `Fault.type` enums, not just transcribed from prose.

DINING_CODES = {
    "SLOT_UNAVAILABLE": ErrorClass.TERMINAL,
    "INVALID_PARTY_SIZE": ErrorClass.TERMINAL,
    "VENUE_CLOSED": ErrorClass.TERMINAL,
    "HOLD_EXPIRED": ErrorClass.REQUIRES_HUMAN,
    "INSIDE_CANCEL_WINDOW": ErrorClass.REQUIRES_HUMAN,
    "RATE_LIMITED": ErrorClass.RETRYABLE,
    "UPSTREAM_ERROR": ErrorClass.RETRYABLE,
}

TICKETING_CODES = {
    "INVENTORY_UNAVAILABLE": ErrorClass.TERMINAL,
    "HOLD_EXPIRED": ErrorClass.REQUIRES_HUMAN,
    "RATE_LIMIT_EXCEEDED": ErrorClass.RETRYABLE,
    "SIGNATURE_INVALID": ErrorClass.TERMINAL,
    "EVENT_CANCELLED": ErrorClass.TERMINAL,
    "NON_REFUNDABLE": ErrorClass.REQUIRES_HUMAN,
    "UPSTREAM_TIMEOUT": ErrorClass.RETRYABLE,
}


def test_dining_publishes_exactly_seven_codes() -> None:
    assert len(DINING_CODES) == 7


def test_ticketing_publishes_exactly_seven_codes() -> None:
    assert len(TICKETING_CODES) == 7


@pytest.mark.parametrize(
    "code,expected", sorted(DINING_CODES.items()), ids=list(DINING_CODES)
)
def test_dining_codes_classify_correctly(code: str, expected: ErrorClass) -> None:
    assert classify(code, None, None) == expected


@pytest.mark.parametrize(
    "code,expected", sorted(TICKETING_CODES.items()), ids=list(TICKETING_CODES)
)
def test_ticketing_codes_classify_correctly(code: str, expected: ErrorClass) -> None:
    assert classify(code, None, None) == expected


def test_known_code_wins_over_a_conflicting_status() -> None:
    """A published code's disposition is authoritative even if an unrelated
    status also happens to be present — the more specific signal wins."""
    assert classify("SLOT_UNAVAILABLE", 500, None) == ErrorClass.TERMINAL


# --- Status-only vendors (amenities, pms, casino publish no codes) ---------


def test_casino_403_is_policy_blocked_never_retryable() -> None:
    """The casino 403 responsible-gaming offer suppression — must never
    classify as retryable (data-model.md is explicit about this)."""
    result = classify(None, 403, None)
    assert result == ErrorClass.POLICY_BLOCKED
    assert result != ErrorClass.RETRYABLE


def test_409_practitioner_taken_or_high_occupancy_is_terminal() -> None:
    assert classify(None, 409, None) == ErrorClass.TERMINAL


def test_410_hold_expired_is_requires_human() -> None:
    assert classify(None, 410, None) == ErrorClass.REQUIRES_HUMAN


def test_429_throttled_is_retryable() -> None:
    assert classify(None, 429, None) == ErrorClass.RETRYABLE


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_is_retryable(status: int) -> None:
    assert classify(None, status, None) == ErrorClass.RETRYABLE


# --- Fallback rule ------------------------------------------------------------


@pytest.mark.parametrize("status", [500, 503, 599])
def test_unknown_code_with_5xx_is_retryable(status: int) -> None:
    assert classify("SOME_FUTURE_CODE", status, None) == ErrorClass.RETRYABLE


@pytest.mark.parametrize("status", [400, 404, 422, 499])
def test_unknown_code_with_4xx_is_terminal(status: int) -> None:
    assert classify("SOME_FUTURE_CODE", status, None) == ErrorClass.TERMINAL


def test_classification_never_raises_on_a_totally_unrecognised_input() -> None:
    """FR-014: an input outside the enumerated set MUST still resolve to a
    disposition, deterministically, on every run."""
    result = classify("WHOLLY_UNKNOWN_CODE_NO_STATUS", None, None)
    assert isinstance(result, ErrorClass)


def test_classification_is_deterministic_across_repeated_calls() -> None:
    args = ("SOME_FUTURE_CODE", 503, None)
    results = {classify(*args) for _ in range(20)}
    assert results == {ErrorClass.RETRYABLE}


# --- System / durability failure modes (durability doc §11) ----------------


def test_all_nine_system_failure_modes_are_present() -> None:
    assert len(SYSTEM_FAILURE_DISPOSITIONS) == 9
    assert set(SYSTEM_FAILURE_DISPOSITIONS) == set(SystemFailureMode)


@pytest.mark.parametrize(
    "mode,expected",
    [
        (SystemFailureMode.HOLD_EXPIRES_DURING_HUMAN_WAIT, ErrorClass.REQUIRES_HUMAN),
        (SystemFailureMode.VENDOR_CONFIRM_TIMES_OUT, ErrorClass.RETRYABLE),
        (SystemFailureMode.CHECKPOINT_WRITE_FAILS, ErrorClass.TERMINAL),
        (SystemFailureMode.COMPENSATION_FAILS, ErrorClass.REQUIRES_HUMAN),
    ],
)
def test_classified_system_failure_modes_resolve_correctly(
    mode: SystemFailureMode, expected: ErrorClass
) -> None:
    assert SYSTEM_FAILURE_DISPOSITIONS[mode] == expected


@pytest.mark.parametrize(
    "mode",
    [
        SystemFailureMode.CRASH_MID_FANOUT_SYNC,
        SystemFailureMode.CRASH_MID_FANOUT_EXIT,
        SystemFailureMode.NODE_FAILS_IN_PARALLEL_SUPERSTEP,
        SystemFailureMode.CRASH_WHILE_INTERRUPTED,
        SystemFailureMode.DEPLOY_DURING_INTERRUPT,
    ],
)
def test_structurally_recovered_modes_are_explicitly_not_classified(
    mode: SystemFailureMode,
) -> None:
    """These five modes never produce a `ToolFailure` needing a disposition
    — recovery is structural (pending writes, the ledger, or version-pin
    comparison), per data-model.md's own mapping note and (for the two
    modes it does not name explicitly — `CRASH_WHILE_INTERRUPTED` and
    `DEPLOY_DURING_INTERRUPT`) per §11's own "Recovery" column, which
    describes both as automatic with no failure object produced. `None`
    here is itself the total, deterministic, tested outcome — not a gap."""
    assert SYSTEM_FAILURE_DISPOSITIONS[mode] is None
