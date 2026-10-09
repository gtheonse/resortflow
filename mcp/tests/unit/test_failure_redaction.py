"""T042 — redaction test (FR-022, SC-013): no guest-identifying value can appear in a
`NormalizedFailure.detail` string.

Exercises `gateway._shared.errors.redact` (the real Presidio-backed
`observability.redaction.redact_text`, not a stub -- Feature 006/ADR-031 already
replaced the original no-op placeholder) and `build_normalized_failure`, the sole
constructor for the envelope every unsuccessful tool call returns.
"""

from __future__ import annotations

from unittest.mock import patch

from gateway._shared.errors import (
    UpstreamFailure,
    build_normalized_failure,
    redact,
)

GUEST_NAME = "Jane Alexandra Doe"
GUEST_PHONE = "555-867-5309"
GUEST_EMAIL = "jane.doe@example.com"


def test_redact_strips_guest_name() -> None:
    detail = f"vendor rejected the request for guest {GUEST_NAME}"
    assert GUEST_NAME not in redact(detail)


def test_redact_strips_phone_number() -> None:
    detail = f"callback failed, guest phone {GUEST_PHONE} unreachable"
    assert GUEST_PHONE not in redact(detail)


def test_redact_strips_email() -> None:
    detail = f"confirmation bounced for {GUEST_EMAIL}"
    assert GUEST_EMAIL not in redact(detail)


def test_redact_preserves_non_identifying_diagnostic_text() -> None:
    """A pure vendor diagnostic with no identifying content should survive
    intact -- redaction is targeted, not wholesale destruction of every detail
    string (the constitution's User Story 4 acceptance criteria: enough
    non-sensitive context remains to diagnose the issue)."""
    detail = "dining rejected the request (409)"
    assert redact(detail) == detail


def test_redact_fails_closed_on_internal_error() -> None:
    """FR-022: if the redactor itself raises, a fixed placeholder is returned --
    never the original, possibly-unredacted text."""
    detail = f"guest {GUEST_NAME} called"
    with patch("observability.redaction.redact_text", side_effect=RuntimeError("boom")):
        result = redact(detail)
    assert result == "[redaction failed — detail suppressed]"
    assert GUEST_NAME not in result


def test_build_normalized_failure_never_carries_a_guest_name() -> None:
    failure = UpstreamFailure(
        error_class="TERMINAL",
        domain="dining",
        detail=f"reservation for {GUEST_NAME} ({GUEST_PHONE}) could not be confirmed",
        vendor_code="SLOT_UNAVAILABLE",
        http_status=409,
    )
    normalized = build_normalized_failure(failure, idempotency_key=None, trace_id=None)
    assert GUEST_NAME not in normalized.detail
    assert GUEST_PHONE not in normalized.detail


def test_build_normalized_failure_never_carries_a_guest_email() -> None:
    failure = UpstreamFailure(
        error_class="RETRYABLE",
        domain="entertainment",
        detail=f"could not notify {GUEST_EMAIL} of the delay",
        http_status=500,
    )
    normalized = build_normalized_failure(failure, idempotency_key="idem-1", trace_id="abc123")
    assert GUEST_EMAIL not in normalized.detail
    # Non-detail fields pass through untouched -- redaction is scoped to `detail`.
    assert normalized.idempotency_key == "idem-1"
    assert normalized.trace_id == "abc123"


def test_completed_sub_calls_carry_no_free_text_detail_field() -> None:
    """`CompletedSubCall` (the FR-030 partial-failure record) has no `detail` or
    free-text field at all -- structurally, there is nothing for a guest value to
    hide inside on that path."""
    from gateway._shared.errors import CompletedSubCall

    fields = set(CompletedSubCall.model_fields)
    assert fields == {"slot_name", "vendor_resource_id", "idempotency_key"}
