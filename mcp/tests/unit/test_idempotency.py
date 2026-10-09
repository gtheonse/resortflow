"""T043 — unit tests for `gateway._shared.idempotency` (`[CRIT]`, FR-015/FR-023's
neighbor: a weak or reconstructed key means duplicate bookings under retry).

Covers:
- key presence/shape enforcement (`validate_idempotency_key`)
- verbatim forwarding as the vendor header (`vendor_header`) — never reshaped
- `sub_call_key` determinism across repeated composite retries
- attempt-component semantics: the module's docstring freezes the caller-side
  format ``resortflow:{session_id}:{hold_id}:{attempt}`` — this module never parses or
  reconstructs it, but the *contract* it must uphold is that the same key is reused
  across a timeout-retry of the same logical attempt, and only a fresh key (a new
  ``attempt`` component minted by the caller) follows a terminal failure. Since this
  module treats the key as an opaque string, this file proves that opacity — any two
  distinct strings are treated as entirely unrelated, so "same key on retry" / "new
  key after terminal failure" is a caller-side property this module never
  undermines by mutating, truncating, or re-deriving the value itself.
"""

from __future__ import annotations

import pytest
from gateway._shared.idempotency import (
    IdempotencyKeyError,
    sub_call_key,
    validate_idempotency_key,
    vendor_header,
)

# --- Presence / shape enforcement (FR-015) ------------------------------------------


def test_validate_accepts_a_well_formed_key() -> None:
    key = "resortflow:sess-1:hold-1:1"
    assert validate_idempotency_key(key) == key


@pytest.mark.parametrize("bad", [None, "", "   ", "\t", "\n"])
def test_validate_rejects_missing_empty_or_whitespace_keys(bad) -> None:
    with pytest.raises(IdempotencyKeyError):
        validate_idempotency_key(bad)


def test_validate_rejects_non_string_types() -> None:
    for bad in (123, 1.5, [], {}, object()):
        with pytest.raises(IdempotencyKeyError):
            validate_idempotency_key(bad)  # type: ignore[arg-type]


def test_validate_rejects_a_key_containing_internal_whitespace() -> None:
    """The frozen format has no internal whitespace; a key that does isn't the shape
    the vendor header contract expects."""
    with pytest.raises(IdempotencyKeyError):
        validate_idempotency_key("resortflow:sess 1:hold-1:1")


def test_validate_returns_the_exact_string_unmodified() -> None:
    """Validation never reshapes the key — it is a gate, not a normalizer."""
    key = "resortflow:SeSs-Weird-Casing:hold-9:3"
    assert validate_idempotency_key(key) is key


# --- Verbatim forwarding (never reshaped) -------------------------------------------


def test_vendor_header_forwards_the_key_verbatim() -> None:
    key = "resortflow:sess-1:hold-1:1"
    assert vendor_header(key) == {"Idempotency-Key": key}


def test_vendor_header_never_alters_case_or_content() -> None:
    key = "resortflow:Sess-MixedCase:hold-XYZ:2"
    header = vendor_header(key)
    assert header["Idempotency-Key"] == key
    assert "idempotency-key" not in header  # exact casing of the header name too


def test_vendor_header_is_the_only_key_in_the_dict() -> None:
    header = vendor_header("resortflow:sess-1:hold-1:1")
    assert list(header.keys()) == ["Idempotency-Key"]


# --- sub_call_key determinism across repeated composite retries --------------------


def test_sub_call_key_is_deterministic_for_the_same_inputs() -> None:
    key = "resortflow:sess-1:hold-1:1"
    assert sub_call_key(key, "create_leg") == sub_call_key(key, "create_leg")


def test_sub_call_key_is_stable_across_many_repeated_calls() -> None:
    """Simulates a composite tool retried many times end-to-end: every retry must
    derive the identical per-slot key so the vendor sees the same Idempotency-Key on
    every attempt of that slot."""
    key = "resortflow:sess-1:hold-1:1"
    derived = {sub_call_key(key, "create_leg") for _ in range(50)}
    assert len(derived) == 1


def test_sub_call_key_differs_across_slots() -> None:
    key = "resortflow:sess-1:hold-1:1"
    assert sub_call_key(key, "create_leg") != sub_call_key(key, "confirm_leg")


def test_sub_call_key_differs_across_top_level_keys_for_the_same_slot() -> None:
    """A fresh top-level key (e.g. after a terminal failure minted a new attempt)
    must derive a fresh sub-call key too — no collision across attempts."""
    key_attempt_1 = "resortflow:sess-1:hold-1:1"
    key_attempt_2 = "resortflow:sess-1:hold-1:2"
    assert sub_call_key(key_attempt_1, "create_leg") != sub_call_key(key_attempt_2, "create_leg")


def test_sub_call_key_format_is_top_level_key_colon_slot_name() -> None:
    """Derivation is the documented `f"{idempotency_key}:{slot_name}"` shape — exact
    and unsurprising, since a future reconciler (ADR-015) keys off this value."""
    assert sub_call_key("resortflow:sess-1:hold-1:1", "create_leg") == "resortflow:sess-1:hold-1:1:create_leg"


# --- Attempt-component semantics: same key across a timeout-retry, new key only ----
# --- after a terminal failure (caller-side contract this module never violates) ----


def test_same_key_reused_across_a_timeout_retry_is_treated_identically() -> None:
    """When the caller retries the same logical attempt after a timeout, it passes
    the *same* idempotency key both times (frozen contract) — this module must
    validate and forward it identically on each call, proving it does no per-call
    mutation that would silently fork the vendor's dedup key."""
    key = "resortflow:sess-1:hold-1:1"
    first_pass = validate_idempotency_key(key)
    second_pass = validate_idempotency_key(key)  # the timeout-retry
    assert first_pass == second_pass == key
    assert vendor_header(first_pass) == vendor_header(second_pass)


def test_new_attempt_component_after_terminal_failure_yields_a_distinct_key() -> None:
    """After a terminal failure, the caller mints a new key with an incremented
    ``attempt`` component (frozen format) — distinct from the exhausted one, and this
    module treats it as an entirely unrelated key (no memory of the prior attempt)."""
    exhausted_key = "resortflow:sess-1:hold-1:1"
    fresh_key = "resortflow:sess-1:hold-1:2"
    assert validate_idempotency_key(exhausted_key) != validate_idempotency_key(fresh_key)
    assert vendor_header(exhausted_key) != vendor_header(fresh_key)
    # And derived sub-call keys track the fresh top-level key, not the exhausted one.
    assert sub_call_key(exhausted_key, "create_leg") != sub_call_key(fresh_key, "create_leg")
