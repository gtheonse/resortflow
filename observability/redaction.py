"""PII redaction at the trace/log write boundary (ADR-031, FR-010, FR-011).

Presidio (NER-backed) analyzer + anonymizer, local model only — this is a
policy-critical redaction pass, not the RAG pipeline, so it must stay fully
offline and deterministic regardless of which embedding/LLM provider happens
to be configured elsewhere (`en_core_web_sm`, vendored at Docker build time
per ADR-031; a local-dev-only gap exists where that vendoring hasn't
landed yet).

Two kinds of free text, two treatments (a refinement made during
implementation):

- A field known BY NAME to carry a raw dietary/health disclosure
  (`_WHOLESALE_REDACT_FIELDS`, e.g. `DietaryRequirement.detail`) is redacted
  wholesale to a fixed placeholder — the spec's own clarification answer
  ("redact identifiers **and free-text disclosures**; retain the structured
  restriction type and severity") asks for the disclosure itself gone, not
  merely names/phones scrubbed out of it.
- Any other free text (a vendor failure message, a general note) goes
  through Presidio's NER-based entity redaction — most of its content is
  operationally useful, only incidental PII within it needs scrubbing.

Presidio's entity list is explicitly scoped to `_REDACTED_ENTITIES` (PERSON,
PHONE_NUMBER, EMAIL_ADDRESS, LOCATION, payment/identity-document numbers, IP
address) — narrower than Presidio's own default set, which also flags
`DATE_TIME`/`URL`/`NRP`/`CRYPTO`; none of those are named in FR-010, and
redacting `DATE_TIME` in particular would strip booking times support staff
need for the "enough non-sensitive context remains to diagnose the issue"
half of this feature's own acceptance criteria (User Story 4, Scenario 2).

Three public functions:
- `redact_text(text)` — the core NER-based primitive, used directly by
  `mcp/gateway/_shared/errors.py`'s `redact()` hook (a bare string in, bare
  string out — that hook's own signature, unchanged).
- `redact_for_trace(payload)` — recursively redacts every string value in a
  dict/list structure: `_ALLOWLIST_FIELDS` pass through untouched (already
  closed-vocabulary, not free text), `_WHOLESALE_REDACT_FIELDS` become a
  fixed placeholder, everything else goes through `redact_text`.
- `redact_for_trace_or_suppress(payload)` — FR-022's fail-closed wrapper.

All raise (or, for the `_or_suppress` variant, return `None`) on failure
rather than silently returning the input unredacted.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

_ALLOWLIST_FIELDS = frozenset(
    {
        "restriction",  # DietaryRequirement.restriction
        "severity",  # DietaryRequirement.severity
        "rule",  # PolicyFlag.rule
        "disposition",  # PolicyFlag.disposition
    }
)
"""Field names exempted from any redaction, matched at any nesting depth —
not because Presidio happened to miss them, but because they are already
closed-vocabulary enum values, not free text, and are required to
investigate a blocked booking (contracts/redaction-contract.md)."""

_WHOLESALE_REDACT_FIELDS = frozenset(
    {
        "detail",  # DietaryRequirement.detail — the guest's own free-text disclosure
    }
)
"""Field names redacted wholesale to a fixed placeholder rather than
NER-scrubbed — a raw dietary/health disclosure is sensitive in its entirety,
not just the identifiers incidentally inside it. NOTE: `ToolFailure.detail`
shares this field name but is a vendor diagnostic message, not a guest
disclosure — `redact_for_trace` cannot distinguish the two by field name
alone when both appear as a bare `"detail"` key. Callers passing a
`ToolFailure`-shaped payload through this function should rename that key
(or call `redact_text` directly) if wholesale redaction would destroy needed
diagnostic content; the two `DietaryRequirement`-specific tests in
`tests/test_redaction.py` cover the case this field name was added for."""

_REDACTED_ENTITIES = (
    "PERSON",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "LOCATION",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_BANK_NUMBER",
    "US_SSN",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
    "US_ITIN",
    "IP_ADDRESS",
)
"""Explicitly scoped to FR-010's named categories (identity, payment,
location) — narrower than Presidio's own default entity set, which also
flags DATE_TIME/URL/NRP/CRYPTO. None of those are FR-010 categories, and
redacting DATE_TIME specifically would strip booking times support staff
need to diagnose an issue (User Story 4, Scenario 2)."""

_WHOLESALE_PLACEHOLDER = "[redacted — free-text disclosure]"


@lru_cache(maxsize=1)
def _get_analyzer() -> Any:
    """Explicitly configured for `en_core_web_sm` — Presidio's own default
    (unconfigured) NLP engine silently prefers `en_core_web_lg` (~400MB,
    auto-downloaded on first use if not already cached), which contradicts
    ADR-031's explicit choice of the small model for image-size/offline-
    footprint reasons. This configuration is what actually enforces that
    choice; omitting it would work by accident only in an environment that
    happened to already have `en_core_web_lg` cached."""
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    return AnalyzerEngine(nlp_engine=provider.create_engine())


@lru_cache(maxsize=1)
def _get_anonymizer() -> Any:
    from presidio_anonymizer import AnonymizerEngine

    # presidio_anonymizer ships no type stubs.
    return AnonymizerEngine()  # type: ignore[no-untyped-call]


def redact_text(text: str) -> str:
    """Redact identifying details, payment details, and free-text health/
    dietary disclosures from a single string. Presidio's default entity set
    (name, phone, email, address, payment card/account references, and
    similar) applied via NER, not keyword matching alone."""
    if not text:
        return text
    analyzer = _get_analyzer()
    anonymizer = _get_anonymizer()
    results = analyzer.analyze(text=text, language="en", entities=list(_REDACTED_ENTITIES))
    if not results:
        return text
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
    return str(anonymized.text)


def redact_for_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact every string value in `payload`, exempting
    `_ALLOWLIST_FIELDS` by key name at any depth (FR-010, FR-011). Applied
    before a diagnostic/observability record is written, never as a later
    cleanup pass."""
    result = _redact_value(payload)
    assert isinstance(result, dict)
    return result


def redact_for_trace_or_suppress(payload: dict[str, Any]) -> dict[str, Any] | None:
    """FR-022 reference implementation for a future trace-write boundary
    (Phase 7 owns the actual emission pipeline — no such call site exists
    yet in this codebase to wire this into directly, per
    the Phase 6 notes). Returns `None` — meaning "suppress this
    record, do not write it" — if `redact_for_trace` itself raises, rather
    than falling back to the raw, unredacted payload. A future emission
    callback should call this and skip the write entirely on `None`."""
    try:
        return redact_for_trace(payload)
    except Exception:  # noqa: BLE001 — fail-closed is the point
        return None


def _redact_value(value: Any, *, field_name: str | None = None) -> Any:
    if field_name is not None and field_name in _ALLOWLIST_FIELDS:
        return value
    if field_name is not None and field_name in _WHOLESALE_REDACT_FIELDS and isinstance(value, str):
        return _WHOLESALE_PLACEHOLDER if value else value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact_value(v, field_name=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value
