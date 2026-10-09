# Interface Contract: `intent_routing_classification` prompt

**Feature**: `010-tiered-intent-routing` | Governed by `specs/009-prompt-management/contracts/*`
(schema, lock, lint, CODEOWNERS). This contract states only what is specific to this prompt.

## Template (`prompts/templates/intent_routing_classification.yaml`)

| Field | Value |
|---|---|
| `id` | `intent_routing_classification` |
| `family` | `routing` (new family) |
| `type` | `chat` |
| `resolution` | `label` |
| `owner` | `platform`: a **new** synthetic group in `prompts/owners.yaml` (two placeholder members, FR-039 of 009), with CODEOWNERS regenerated. The existing groups (`compliance`, `eval_track`, `incident`) do not own routing |
| `config` | `{temperature: 0, response_format: {type: json_object}}` |
| `variables.runtime` | `guest_message`, `previous_context` |
| code-injected required vars | `allowed_kinds`, `allowed_services` (rendered from `RequestKind` / `Domain` every call; `declare_required` at the call site) |

Messages: the **system** message states the task, the JSON shape
`{"kind": <one of {{allowed_kinds}}>, "services": [<zero or more of {{allowed_services}}>]}`, that
`out_of_scope` means not a resort service, and that the classifier must never output anything
else. The **user** message is `Previous turn: {{previous_context}}\nGuest: {{guest_message}}`.
Guest text appears only in the user message (ADR-032). The prompt has no tools.

`previous_context` is exactly `kind=<kind>; services=<comma list>`, or `none`. It never contains
message text (FR-008b).

## Runtime binding

- Model: `MODEL_CHEAP` (default `gpt-4.1-mini`). **Economy runtime tier, static** (Principle VI).
  The model is chosen by code; a prompt `config` cannot set it (009's allow-list).
- Span: `start_generation_span("routing.intent_classification", prompt=resolved, model=…,
  node="intent_router", domain="supervisor")`, which carries `gen_ai.*` and `resortflow.prompt.*`.
- Timeout 1.5 s. No retry, no escalation (research R-07).

## Lock and gate

- Entry added to `prompts/prompts.lock.json` via the 009 sync flow. A change is gated by
  `routing_accuracy` on the `requires_tier2` cases (credentials present) and by the degraded-prompt
  fixture (research R-13).
- Lint (V1–V9) must pass; `allowed_kinds` / `allowed_services` are required vars, so a template
  missing them fails closed to the Git copy (`missing_required_var`).
