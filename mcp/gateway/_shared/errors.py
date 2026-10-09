"""The four-class error taxonomy: classification and `NormalizedFailure` construction.

`[CRIT]` per `docs/models.md`'s "Error taxonomy and classification" row — mis-classifying
TERMINAL as RETRYABLE produces infinite retry against a vendor; mis-classifying a real
POLICY_BLOCKED case as RETRYABLE retries a responsible-gaming suppression, which the
constitution forbids outright.

Classification is total: every vendor outcome resolves to exactly one of the four classes,
with an explicit fallback for any code this table doesn't name (FR-033). No vendor envelope
shape (dining's ``{"error": {...}}``, ticketing's ``{"fault": {...}}``, or the flat/legacy
shapes used by amenities/pms/casino) ever reaches a caller — everything is normalized to
`NormalizedFailure`, per `docs/contracts/schemas/tool-error.schema.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ValidationError

ErrorClass = Literal["RETRYABLE", "TERMINAL", "REQUIRES_HUMAN", "POLICY_BLOCKED"]

# --- Published codes (specs/001-contracts-foundation/data-model.md) -----------------

PUBLISHED_CODES: dict[str, ErrorClass] = {
    "SLOT_UNAVAILABLE": "TERMINAL",
    "INVALID_PARTY_SIZE": "TERMINAL",
    "VENUE_CLOSED": "TERMINAL",
    "HOLD_EXPIRED": "REQUIRES_HUMAN",
    "INSIDE_CANCEL_WINDOW": "REQUIRES_HUMAN",
    "RATE_LIMITED": "RETRYABLE",
    "RATE_LIMIT_EXCEEDED": "RETRYABLE",
    "UPSTREAM_ERROR": "RETRYABLE",
    "UPSTREAM_TIMEOUT": "RETRYABLE",
    "INVENTORY_UNAVAILABLE": "TERMINAL",
    "EVENT_CANCELLED": "TERMINAL",
    "NON_REFUNDABLE": "REQUIRES_HUMAN",
    "SIGNATURE_INVALID": "TERMINAL",
}


def classify_status(
    http_status: int, *, domain: str, vendor_code: str | None = None
) -> ErrorClass:
    """Classify a failing vendor response by status (and code, when one exists).

    Total: every branch is covered, ending in the unknown-code fallback (FR-033) —
    unknown + 5xx -> RETRYABLE, unknown + 4xx -> TERMINAL. Never raises: an unrecognized
    code from a vendor's minor release must not become a ResortFlow outage.
    """
    if vendor_code is not None and vendor_code in PUBLISHED_CODES:
        return PUBLISHED_CODES[vendor_code]

    # The one architecturally load-bearing status-only case: casino's 403 on offers
    # means "responsible-gaming suppression active" — never retryable (FR-019).
    if domain == "gaming" and http_status == 403:
        return "POLICY_BLOCKED"

    if http_status == 409:
        return "TERMINAL"
    if http_status == 410:
        return "REQUIRES_HUMAN"
    if http_status == 429:
        return "RETRYABLE"
    if 500 <= http_status < 600:
        return "RETRYABLE"
    if 400 <= http_status < 500:
        return "TERMINAL"
    # Never expected in practice (informational/redirect codes don't reach a failure
    # path) but classification must still be total rather than raise.
    return "TERMINAL"


@dataclass(frozen=True)
class BodyClassification:
    error_class: ErrorClass | None
    """``None`` means the body parsed and validated — not a failure at all."""
    model: BaseModel | None = None
    detail: str = ""


def classify_body(raw_body: bytes, *, model_cls: type[BaseModel]) -> BodyClassification:
    """Response-body classification (FR-034/035/036).

    - JSON decode failure -> RETRYABLE (a transient wire-level corruption is worth a
      retry; FR-034).
    - Schema-invalid-but-parseable body -> TERMINAL (FR-035) — retrying a shape the
      vendor's own contract doesn't produce cannot succeed differently.
    - No field is ever extracted from a failing body (FR-036): the only way a value
      leaves this function is via the fully-validated `model`.
    """
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return BodyClassification(error_class="RETRYABLE", detail="response body was not valid JSON")

    try:
        validated = model_cls.model_validate(parsed)
    except ValidationError:
        return BodyClassification(error_class="TERMINAL", detail="response body failed schema validation")

    return BodyClassification(error_class=None, model=validated)


class CompletedSubCall(BaseModel):
    slot_name: str
    vendor_resource_id: str
    idempotency_key: str


class NormalizedFailure(BaseModel):
    """Producer-side model of the schema this feature is the sole producer of
    (`docs/contracts/schemas/tool-error.schema.json`, amended by ADR-015)."""

    error_class: ErrorClass
    domain: str
    detail: str
    vendor_code: str | None = None
    http_status: int | None = None
    retry_after_seconds: int | None = None
    idempotency_key: str | None = None
    trace_id: str | None = None
    completed_sub_calls: list[CompletedSubCall] | None = None
    effect_status: Literal["unresolved"] | None = None


@dataclass
class UpstreamFailure(Exception):
    """Raised internally by `_shared.upstream` and caught at the tool-registration
    boundary (`registry.py`) to build the caller-visible `NormalizedFailure`."""

    error_class: ErrorClass
    domain: str
    detail: str
    vendor_code: str | None = None
    http_status: int | None = None
    retry_after_seconds: int | None = None
    completed_sub_calls: list[CompletedSubCall] = field(default_factory=list)
    effect_status: Literal["unresolved"] | None = None

    def __str__(self) -> str:  # pragma: no cover — debugging aid only
        return f"{self.error_class}: {self.detail}"


def redact(detail: str) -> str:
    """Feature 006 (ADR-031): real Presidio-backed redaction, replacing this
    hook's original no-op placeholder. This feature's own failure
    construction still never places a guest-identifying value into `detail`
    in the first place today (vendor diagnostics are codes/messages, not
    guest records) — this call is what catches a future guest-identifying
    vendor field without every call site needing to remember to redact it
    itself.

    Fails closed (FR-022): if redaction itself raises, this returns a fixed
    placeholder rather than the original (possibly unredacted) text — never
    write the input through unredacted just because the redactor errored."""
    from observability.redaction import redact_text

    try:
        return redact_text(detail)
    except Exception:  # noqa: BLE001 — fail-closed is the point; any error suppresses
        return "[redaction failed — detail suppressed]"


def build_normalized_failure(
    failure: UpstreamFailure,
    *,
    idempotency_key: str | None,
    trace_id: str | None,
) -> NormalizedFailure:
    """The one constructor for the envelope every unsuccessful tool call returns."""
    return NormalizedFailure(
        error_class=failure.error_class,
        domain=failure.domain,
        detail=redact(failure.detail),
        vendor_code=failure.vendor_code,
        http_status=failure.http_status,
        retry_after_seconds=failure.retry_after_seconds if failure.error_class == "RETRYABLE" else None,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
        completed_sub_calls=failure.completed_sub_calls or None,
        effect_status=failure.effect_status,
    )
