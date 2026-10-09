"""T039 — classification unit tests for the four-class error taxonomy
(`gateway._shared.errors`).

Covers, per `specs/001-contracts-foundation/data-model.md`:
- every published vendor code (dining + ticketing's 11-code table)
- both status-only tables (the casino `403` case and the amenities/pms
  `409`/`410`/`429`/`5xx` table)
- the casino `403 -> POLICY_BLOCKED` case specifically (FR-019, SC-007)
- the unknown-code fallback in both directions (`5xx -> RETRYABLE`,
  `4xx -> TERMINAL`), never raising (FR-033)
- response-body classification (FR-034/035/036)
"""

from __future__ import annotations

import pytest
from gateway._shared.errors import (
    PUBLISHED_CODES,
    classify_body,
    classify_status,
)
from pydantic import BaseModel

# --- Published codes (data-model.md "Published codes" table) -----------------------

PUBLISHED_CODE_TABLE = [
    ("SLOT_UNAVAILABLE", "dining", "TERMINAL"),
    ("INVALID_PARTY_SIZE", "dining", "TERMINAL"),
    ("VENUE_CLOSED", "dining", "TERMINAL"),
    ("HOLD_EXPIRED", "dining", "REQUIRES_HUMAN"),
    ("HOLD_EXPIRED", "entertainment", "REQUIRES_HUMAN"),
    ("INSIDE_CANCEL_WINDOW", "dining", "REQUIRES_HUMAN"),
    ("RATE_LIMITED", "dining", "RETRYABLE"),
    ("RATE_LIMIT_EXCEEDED", "entertainment", "RETRYABLE"),
    ("UPSTREAM_ERROR", "dining", "RETRYABLE"),
    ("UPSTREAM_TIMEOUT", "entertainment", "RETRYABLE"),
    ("INVENTORY_UNAVAILABLE", "entertainment", "TERMINAL"),
    ("EVENT_CANCELLED", "entertainment", "TERMINAL"),
    ("NON_REFUNDABLE", "entertainment", "REQUIRES_HUMAN"),
    ("SIGNATURE_INVALID", "entertainment", "TERMINAL"),
]


@pytest.mark.parametrize("code,domain,expected", PUBLISHED_CODE_TABLE)
def test_published_code_classification(code: str, domain: str, expected: str) -> None:
    """Every published code classifies per data-model.md regardless of the HTTP
    status it happens to arrive with — code takes precedence over status."""
    # Arbitrary status accompanying the code; classification must be code-driven.
    assert classify_status(499, domain=domain, vendor_code=code) == expected
    assert classify_status(500, domain=domain, vendor_code=code) == expected


def test_all_fourteen_published_codes_covered() -> None:
    """The transcribed table (data-model.md) names 13 distinct published codes
    (`RATE_LIMITED`/`RATE_LIMIT_EXCEEDED` count separately) -- assert the module's
    own table has exactly this set, so this test fails loudly if a future edit
    drops or silently renames one."""
    assert set(PUBLISHED_CODES) == {
        "SLOT_UNAVAILABLE",
        "INVALID_PARTY_SIZE",
        "VENUE_CLOSED",
        "HOLD_EXPIRED",
        "INSIDE_CANCEL_WINDOW",
        "RATE_LIMITED",
        "RATE_LIMIT_EXCEEDED",
        "UPSTREAM_ERROR",
        "UPSTREAM_TIMEOUT",
        "INVENTORY_UNAVAILABLE",
        "EVENT_CANCELLED",
        "NON_REFUNDABLE",
        "SIGNATURE_INVALID",
    }


# --- Status-only tables --------------------------------------------------------------


def test_casino_403_is_policy_blocked() -> None:
    """The single architecturally load-bearing status-only case (FR-019):
    casino's 403 on an offers-shaped call means responsible-gaming suppression,
    never retryable."""
    assert classify_status(403, domain="gaming") == "POLICY_BLOCKED"


def test_non_gaming_403_is_not_policy_blocked() -> None:
    """POLICY_BLOCKED is specific to the gaming domain's 403 -- a 403 from any
    other domain is an ordinary 4xx and falls to the generic TERMINAL branch."""
    for domain in ("dining", "entertainment", "stay", "wellness"):
        assert classify_status(403, domain=domain) == "TERMINAL"


@pytest.mark.parametrize("domain", ["dining", "entertainment", "stay", "wellness", "gaming"])
def test_status_only_table_409_terminal(domain: str) -> None:
    assert classify_status(409, domain=domain) == "TERMINAL"


@pytest.mark.parametrize("domain", ["dining", "entertainment", "stay", "wellness", "gaming"])
def test_status_only_table_410_requires_human(domain: str) -> None:
    assert classify_status(410, domain=domain) == "REQUIRES_HUMAN"


@pytest.mark.parametrize("domain", ["dining", "entertainment", "stay", "wellness", "gaming"])
def test_status_only_table_429_retryable(domain: str) -> None:
    assert classify_status(429, domain=domain) == "RETRYABLE"


@pytest.mark.parametrize("domain", ["dining", "entertainment", "stay", "wellness", "gaming"])
@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
def test_status_only_table_5xx_retryable(domain: str, status: int) -> None:
    assert classify_status(status, domain=domain) == "RETRYABLE"


# --- Unknown-code fallback, both directions (FR-033) ----------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 599])
def test_unknown_code_5xx_falls_back_to_retryable(status: int) -> None:
    assert classify_status(status, domain="dining", vendor_code="TOTALLY_UNKNOWN_CODE") == "RETRYABLE"
    assert classify_status(status, domain="dining", vendor_code=None) == "RETRYABLE"


@pytest.mark.parametrize("status", [400, 402, 404, 422, 499])
def test_unknown_code_4xx_falls_back_to_terminal(status: int) -> None:
    assert classify_status(status, domain="dining", vendor_code="TOTALLY_UNKNOWN_CODE") == "TERMINAL"
    assert classify_status(status, domain="dining", vendor_code=None) == "TERMINAL"


def test_classify_status_never_raises_on_unusual_status() -> None:
    """Classification is total (FR-033) -- an informational/redirect status never
    expected in a failure path still classifies rather than raising."""
    for status in (100, 200, 204, 301, 302):
        result = classify_status(status, domain="dining")
        assert result in ("RETRYABLE", "TERMINAL", "REQUIRES_HUMAN", "POLICY_BLOCKED")


# --- Response-body classification (FR-034/035/036) ------------------------------------


class _Model(BaseModel):
    model_config = {"extra": "forbid"}
    value: str


def test_classify_body_json_decode_failure_is_retryable() -> None:
    result = classify_body(b"not { valid json", model_cls=_Model)
    assert result.error_class == "RETRYABLE"
    assert result.model is None


def test_classify_body_invalid_utf8_is_retryable() -> None:
    result = classify_body(b"\xff\xfe\x00\x01", model_cls=_Model)
    assert result.error_class == "RETRYABLE"
    assert result.model is None


def test_classify_body_schema_invalid_is_terminal() -> None:
    result = classify_body(b'{"unexpected_field": 1}', model_cls=_Model)
    assert result.error_class == "TERMINAL"
    assert result.model is None


def test_classify_body_valid_returns_none_with_model() -> None:
    result = classify_body(b'{"value": "ok"}', model_cls=_Model)
    assert result.error_class is None
    assert result.model is not None
    assert result.model.value == "ok"  # type: ignore[union-attr]


def test_classify_body_never_extracts_a_field_from_a_failing_body() -> None:
    """FR-036: the only way a value leaves `classify_body` is via the fully
    validated `model` -- a failing body's `detail` is a fixed diagnostic string,
    never a value lifted out of the untrusted payload."""
    raw = b'{"guest_name": "Jane Doe", "unexpected_field": 1}'
    result = classify_body(raw, model_cls=_Model)
    assert result.error_class == "TERMINAL"
    assert "Jane Doe" not in result.detail
    assert "guest_name" not in result.detail
