"""T006 + T019 — the four-way error taxonomy, ResortFlow's exception hierarchy,
and the pure classifier.

FR-013, FR-014: every failure classifies into exactly one of four
dispositions — retryable, terminal, requires-human, policy-blocked — and
classification is total and deterministic. No internal imports.

T006 built ``ErrorClass`` and the exception hierarchy. T019 (User Story 3)
adds ``classify()`` and ``SYSTEM_FAILURE_DISPOSITIONS``, transcribed from
``data-model.md``'s "Error taxonomy mapping" section, itself the union of
two authoritative sources (FR-014, spec.md Assumptions):

1. The failure-code enumerations published by dining and ticketing (7 codes
   each), plus the status-only conventions of amenities/pms/casino.
2. The nine system/durability failure modes in
   ``docs/ResortFlow-HITL-Durability-State.md`` §11.

``classify(vendor_code, http_status, retryable_hint) -> ErrorClass`` covers
source (1) only — it is a *vendor* classifier, and a system failure mode is
never a vendor HTTP response. Source (2) is covered separately by
``SYSTEM_FAILURE_DISPOSITIONS``, keyed by ``SystemFailureMode`` rather than
threaded through ``classify()`` with synthetic codes.
"""

from __future__ import annotations

from enum import Enum


class ErrorClass(str, Enum):
    """The four dispositions every classified failure resolves to.

    Transcribed from ``data-model.md``'s ``ErrorClass`` enumeration.
    """

    RETRYABLE = "RETRYABLE"
    TERMINAL = "TERMINAL"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"
    POLICY_BLOCKED = "POLICY_BLOCKED"


class ResortFlowError(Exception):
    """Base of ResortFlow's exception hierarchy.

    Carries the fields every classified failure needs to be reported,
    retried, escalated or logged without re-deriving them from the vendor
    response a second time.
    """

    def __init__(
        self,
        message: str,
        *,
        vendor_code: str | None = None,
        http_status: int | None = None,
        domain: str | None = None,
        detail: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.vendor_code = vendor_code
        self.http_status = http_status
        self.domain = domain
        self.detail = detail
        self.retry_after = retry_after


class RetryableError(ResortFlowError):
    """The failure may succeed if retried — e.g. rate limiting, an upstream
    timeout with an idempotency key making retry safe."""


class TerminalError(ResortFlowError):
    """The failure cannot succeed on retry — e.g. an invalid party size, a
    closed venue, a signature mismatch caused by configuration drift."""


class RequiresHumanError(ResortFlowError):
    """The failure requires a human decision — e.g. inventory lost after the
    guest saw a plan, or a non-refundable charge at stake."""


class PolicyBlockedError(ResortFlowError):
    """The failure is a compliance refusal, never a retryable condition —
    e.g. the casino ``403`` responsible-gaming offer suppression."""


# --- T019: classify() (FR-013, FR-014) ---------------------------------------

_KNOWN_CODE_DISPOSITIONS: dict[str, ErrorClass] = {
    # dining-sim.openapi.yaml Error.code — 7 codes
    "SLOT_UNAVAILABLE": ErrorClass.TERMINAL,
    "INVALID_PARTY_SIZE": ErrorClass.TERMINAL,
    "VENUE_CLOSED": ErrorClass.TERMINAL,
    "INSIDE_CANCEL_WINDOW": ErrorClass.REQUIRES_HUMAN,
    "RATE_LIMITED": ErrorClass.RETRYABLE,
    "UPSTREAM_ERROR": ErrorClass.RETRYABLE,
    # ticketing-sim.openapi.yaml Fault.type — 7 codes (HOLD_EXPIRED shared
    # with dining above; both vendors publish the identical string with the
    # identical disposition)
    "HOLD_EXPIRED": ErrorClass.REQUIRES_HUMAN,
    "INVENTORY_UNAVAILABLE": ErrorClass.TERMINAL,
    "EVENT_CANCELLED": ErrorClass.TERMINAL,
    "NON_REFUNDABLE": ErrorClass.REQUIRES_HUMAN,
    "SIGNATURE_INVALID": ErrorClass.TERMINAL,
    "RATE_LIMIT_EXCEEDED": ErrorClass.RETRYABLE,
    "UPSTREAM_TIMEOUT": ErrorClass.RETRYABLE,
}
"""The union of dining's 7 and ticketing's 7 published codes (13 distinct
strings — `HOLD_EXPIRED` is published by both, with the same disposition).
Transcribed from data-model.md's "Error taxonomy mapping", cross-checked
against both specs' own `Error.code` / `Fault.type` enums directly."""


def classify(
    vendor_code: str | None,
    http_status: int | None,
    retryable_hint: bool | None,
) -> ErrorClass:
    """Resolve a vendor failure to exactly one of the four dispositions.
    Total and deterministic (FR-014) — never raises, and the same input
    always resolves to the same output.

    Precedence, most to least specific:

    1. A recognized `vendor_code` wins outright, regardless of `http_status`
       or `retryable_hint` — dining and ticketing publish codes precisely so
       a caller doesn't have to guess from status alone.
    2. Status-only conventions for the three vendors that publish no codes
       at all (amenities, pms, casino) — also the fallback for a code this
       module doesn't recognize, since three of five vendors are
       status-only by design (data-model.md's "Error taxonomy mapping").
       `403` is the casino/offers responsible-gaming suppression and MUST
       resolve to `POLICY_BLOCKED`, never `RETRYABLE` — retrying a
       responsible-gaming refusal is both futile and exactly what the
       constitution forbids.
    3. The general fallback: unknown code with a `5xx` status ->
       `RETRYABLE`; unknown code with a `4xx` status -> `TERMINAL`. An
       unrecognised code from a vendor's minor release must not become a
       ResortFlow outage.
    4. No code and no status at all: `retryable_hint` (the vendor's own
       boolean, e.g. dining's `error.retryable`) is the last signal
       available. Absent even that, default to `TERMINAL` — a disposition
       that stops rather than loops is the safer unknown.
    """
    if vendor_code is not None and vendor_code in _KNOWN_CODE_DISPOSITIONS:
        return _KNOWN_CODE_DISPOSITIONS[vendor_code]

    if http_status is not None:
        if http_status == 403:
            return ErrorClass.POLICY_BLOCKED
        if http_status == 409:
            return ErrorClass.TERMINAL
        if http_status == 410:
            return ErrorClass.REQUIRES_HUMAN
        if http_status == 429:
            return ErrorClass.RETRYABLE
        if http_status >= 500:
            return ErrorClass.RETRYABLE
        if http_status >= 400:
            return ErrorClass.TERMINAL

    if retryable_hint is True:
        return ErrorClass.RETRYABLE

    return ErrorClass.TERMINAL


class SystemFailureMode(str, Enum):
    """The nine system/durability failure modes enumerated in
    ``docs/ResortFlow-HITL-Durability-State.md`` §11's Failure Matrix — the
    second half of FR-014's coverage union. Never a vendor HTTP response,
    so never routed through `classify()`."""

    CRASH_MID_FANOUT_SYNC = "crash_mid_fanout_sync"
    CRASH_MID_FANOUT_EXIT = "crash_mid_fanout_exit"
    NODE_FAILS_IN_PARALLEL_SUPERSTEP = "node_fails_in_parallel_superstep"
    CRASH_WHILE_INTERRUPTED = "crash_while_interrupted"
    HOLD_EXPIRES_DURING_HUMAN_WAIT = "hold_expires_during_human_wait"
    VENDOR_CONFIRM_TIMES_OUT = "vendor_confirm_times_out"
    DEPLOY_DURING_INTERRUPT = "deploy_during_interrupt"
    CHECKPOINT_WRITE_FAILS = "checkpoint_write_fails"
    COMPENSATION_FAILS = "compensation_fails"


SYSTEM_FAILURE_DISPOSITIONS: dict[SystemFailureMode, ErrorClass | None] = {
    # Recovered structurally — pending writes preserve successful nodes and
    # the ledger has every intent, so no failure object is ever produced
    # for these to classify. data-model.md names these two explicitly
    # ("recovered by pending writes and the ledger rather than classified").
    SystemFailureMode.CRASH_MID_FANOUT_SYNC: None,
    SystemFailureMode.CRASH_MID_FANOUT_EXIT: None,
    SystemFailureMode.NODE_FAILS_IN_PARALLEL_SUPERSTEP: None,
    # Not named explicitly in data-model.md's mapping note (a gap in that
    # summary — flagged during T018/T019 implementation rather than
    # silently resolved). Inferred from §11's own "Recovery" column, which
    # describes both as automatic and structural, producing no failure
    # object: an interrupt is itself an exit that persists state normally,
    # and a mid-interrupt deploy is settled by version-pin comparison
    # (drain or explicit reject) — neither is a vendor/tool failure.
    SystemFailureMode.CRASH_WHILE_INTERRUPTED: None,
    SystemFailureMode.DEPLOY_DURING_INTERRUPT: None,
    # These four *do* produce a classified outcome.
    SystemFailureMode.HOLD_EXPIRES_DURING_HUMAN_WAIT: ErrorClass.REQUIRES_HUMAN,
    SystemFailureMode.VENDOR_CONFIRM_TIMES_OUT: ErrorClass.RETRYABLE,
    SystemFailureMode.CHECKPOINT_WRITE_FAILS: ErrorClass.TERMINAL,
    SystemFailureMode.COMPENSATION_FAILS: ErrorClass.REQUIRES_HUMAN,
}
"""`None` is itself the total, tested outcome for a structurally-recovered
mode — not a gap. See `tests/test_errors.py`'s
`test_structurally_recovered_modes_are_explicitly_not_classified`."""
