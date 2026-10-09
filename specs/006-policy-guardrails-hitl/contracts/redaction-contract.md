# Contract: PII redaction at the trace/log write boundary

## Where it runs

One function, `observability/redaction.py:redact_for_trace(payload: dict) -> dict`,
called at every point a span/log record is about to be written — the same point
`mcp/gateway/_shared/errors.py:147`'s long-stubbed `redact()` hook was reserved for
(this feature implements it, not a parallel mechanism). Redaction happens **before**
persistence (FR-011) — there is no "redact later" pass, and per the fail-closed
clarification (FR-022), if redaction cannot be applied (e.g., the redaction call itself
raises), the record is suppressed rather than written unredacted.

## What is redacted

Presidio's default entity set applied to free-text fields: guest name, phone, email,
physical address, payment card/account references, and free-text health/dietary
disclosures (the conversational text a guest used to describe a restriction — not the
structured field derived from it, see below).

## What is exempt (the field-level allowlist)

Per the spec clarification (redaction scope), these fields are passed through
**unredacted** because they are already-typed, closed-vocabulary values, not free text,
and are not identifying once the surrounding identifiers are removed:

| Field | Model | Why exempt |
|---|---|---|
| `DietaryRequirement.restriction` | `graph/models.py` | Closed enum (e.g. `shellfish_allergy`), required to investigate a blocked booking (SC-002, SC-004) |
| `DietaryRequirement.severity` | `graph/models.py` | Closed enum (`ALLERGY_SEVERE`, etc.) |
| `PolicyFlag.rule` | `graph/models.py` | Closed vocabulary (data-model.md) — the reason a flag fired, needed to audit an escalation |
| `PolicyFlag.disposition` | `graph/models.py` | Closed vocabulary (`blocked`/`redacted`/`escalated`/`messaged`) |

The allowlist is implemented as an explicit field-name check in
`redact_for_trace` — a field reaches the trace unredacted only because it is named on
this list, never because the NLP pass happened not to flag it. This is the same
"structural, not incidental" property ADR-029 applies to the allergen-override
guarantee, applied here to the redaction guarantee.

## What is NOT covered by this contract

- `ToolFailure.detail` — already carries a "Redacted before persistence" docstring
  promise (graph/models.py); this feature is what makes that promise true, by routing
  `detail` through `redact_for_trace` before it reaches `saga_log`/any trace, same as
  every other free-text field above.
- Redaction of conversation state itself (`ResortFlowState.messages`) is out of scope — the
  checkpointer's `EncryptedSerializer` (Constitution Principle V) is the existing control
  for conversation-state-at-rest; this contract governs the separate
  diagnostic/observability trail (Langfuse/OTel), which the constitution and this
  feature's exit criteria treat as a distinct, lower-trust destination than the
  encrypted checkpoint store.
