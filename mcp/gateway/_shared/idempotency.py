"""Idempotency-key handling. `[CRIT]` per `docs/models.md` — a weak key means duplicate
bookings under retry.

Elaborates ADR-006's frozen top-level formula (`data-model.md`): the caller computes
``resortflow:{session_id}:{hold_id}:{attempt}`` once and passes it as a single argument — this
module never reconstructs it from separate parts, and never sees those parts
independently. It validates presence/shape, forwards the value verbatim as the vendor
``Idempotency-Key`` header, and derives per-sub-call keys for composite tools.
"""

from __future__ import annotations

import re

IDEMPOTENCY_KEY_RE = re.compile(r"^\S+$")


class IdempotencyKeyError(Exception):
    """Raised when a mutating tool is invoked without a valid idempotency key."""


def validate_idempotency_key(key: str | None) -> str:
    """Presence/shape validation (FR-015). A mutating tool call with no key, or a key
    that is empty/whitespace, is refused before any upstream call is made."""
    if key is None or not isinstance(key, str) or not IDEMPOTENCY_KEY_RE.match(key):
        raise IdempotencyKeyError("idempotency_key is required and must be a non-empty string")
    return key


def vendor_header(key: str) -> dict[str, str]:
    """The key is forwarded to the vendor verbatim — never reshaped, never re-derived."""
    return {"Idempotency-Key": key}


def sub_call_key(idempotency_key: str, slot_name: str) -> str:
    """Per-sub-call key for a composite tool (research.md D8): deterministic across
    every retry of the whole composite, since `slot_name` is fixed at tool-definition
    time rather than derived from a vendor response."""
    return f"{idempotency_key}:{slot_name}"
