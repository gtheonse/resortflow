# Contract: Tier-2 classifier prompt — delta for feature 011

Amends [routing-classifier-prompt.md](../../010-tiered-intent-routing/contracts/routing-classifier-prompt.md).
Template: `prompts/templates/intent_routing_classification.yaml` (owner `platform`, family `routing`), revised
through the feature-009 registry flow (`python -m prompts.sync validate` must pass; lint V1–V9).

## Changes

1. **Allowed kinds** gains `browse`. `allowed_kinds` is a code-injected required variable built from the
   `RequestKind` enum, so the closed set, the `ClassifierOutput` schema validation and the fail-closed
   behaviour follow automatically; the template text needs no hard-coded list.
2. **One definition line** in the kind glossary: `"browse" — the guest wants to see what is available or on
   offer (a list of venues, events, treatments, rooms, or the resort's experiences) before choosing; nothing
   is being booked, changed or asked about a single known item.`
3. **One rule** in the instructions: `Requests to list, show, or see what the resort offers are in scope.
   Never return "out_of_scope" for them; use "browse".` `out_of_scope` stays "clearly unrelated to a resort
   guest's needs (flights, weather, visas, …)".
4. **Previous-context line unchanged** (only the previous turn's kind and services, never message text —
   FR-008b of feature 010). A previous `browse` is now a possible value there.

## Unchanged (and re-asserted by tests)

`gpt-4.1-mini`, deterministic settings, 1.5 s timeout, no retry, no escalation; output JSON validated in code
against the enum and service set; any failure → `UNAVAILABLE`/`INVALID` → `CLARIFY`; a `tenant_id`/`guest_id`
key in the output is rejected; guest text only ever in the user message.

## Timeout decision (R-08)

Not changed by this contract. At calibration the `UNAVAILABLE` rate is measured over the browse cases that
reach tier 2 (there should be few: deterministic cues cover the common phrasings). If more than 5% of those
time out, the timeout change is recorded as its own decision with the measurement, not bundled silently.

## Tests

`prompts/tests/test_routing_prompt.py` (extended): the rendered prompt contains `browse` among the injected
kinds, contains the in-scope rule, still fails closed if `allowed_kinds` is missing; the failure-matrix test
adds a `browse` output with a non-empty service list that includes an unknown service (→ `INVALID`) and a
valid `browse` (→ `DECIDED`, follows the normal browse path).
