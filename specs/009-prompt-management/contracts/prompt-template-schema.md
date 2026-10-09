# Contract: Prompt template file

Location: `prompts/templates/<id>.yaml`. One file per prompt. Canonical source of truth (FR-002).
The text of the dining prompt below is transcribed from
`agents/dining/nodes/intent_parser.py:211-219` with the two f-string interpolations replaced by
named placeholders; wording is otherwise unchanged so the migration is behaviour-neutral.

## Schema

```yaml
id: dining_intent_extraction          # snake_case; == filename stem; no "/"
family: dining                        # dining | eval_retrieval_judge | eval_trajectory_judge
type: chat
resolution: label                     # label | exact   (exact only for judge families)
owner: compliance                     # key in prompts/owners.yaml
config:                               # allowed keys: temperature, response_format. NO model / model_tier (S-1)
  temperature: 0
  response_format: { type: json_object }
variables:
  runtime: [guest_message]            # data supplied by the call site
  tenant: []                          # TenantProfile field names; empty for every in-scope template today
messages:
  - role: system
    content: |
      Extract structured dining-reservation intent from the guest message. Respond with ONLY a JSON object with keys: cuisine (string|null), party_size (integer|null), date_str (YYYY-MM-DD string|null), time_str (HH:MM 24h string|null), occasion (string|null), dietary (array of {restriction, severity, detail} using restriction in {{dietary_restrictions}} and severity in {{dietary_severities}}).
  - role: user
    content: "{{guest_message}}"
```

`dietary_restrictions` and `dietary_severities` are **not** listed under `variables`. They are the
code-injected *required* variables declared at the call site (FR-007/FR-008), so a template
edit cannot remove the obligation.

## Judge example

```yaml
id: eval_retrieval_judge_context_recall
family: eval_retrieval_judge
type: chat
resolution: exact
owner: eval_track                     # a rubric that scores dietary/allergen criteria uses `owner: compliance` instead (FR-036b)
config: { temperature: 0 }
variables:
  runtime: [question, context_block, facts_block]
  tenant: []
messages:
  - role: system
    content: |
      You are a strict evaluation judge for a hospitality RAG system. Respond with ONLY a JSON object of the shape {"score": <float 0.0-1.0>, "reasoning": "<one sentence>"}. No prose outside the JSON.
  - role: user
    content: |
      Question: {{question}}

      Retrieved context:
      {{context_block}}

      Facts a correct, complete answer must draw on:
      {{facts_block}}

      Score the RECALL of the retrieved context: what fraction of the required facts are actually present (even partially) somewhere in the retrieved context? 1.0 = every fact is supported; 0.0 = none are.
```

## Validation rules (enforced by `prompts.sync validate`, at process start, and in CI)

| # | Rule | Failure |
|---|---|---|
| V1 | Unknown top-level key, or `config` key outside `{temperature, response_format}` | reject |
| V2 | Placeholder not in `required ∪ runtime ∪ tenant` declared for the call site | reject (`undeclared_var`) |
| V3 | A call-site `required_var` absent from the template | reject (`missing_required_var`) |
| V4 | The template references a placeholder for which no value was supplied (checked on the template's placeholder set **before** substitution; runtime values are never scanned) | reject (`unrendered_placeholder`) |
| V5 | Literal `DietaryRestriction` / `DietarySeverity` value in message text, no matching exemption | lint fail |
| V6 | Tenant identifier (`MGV`, `MHB`), jurisdiction or regulator name in message text | lint fail |
| V7 | `resolution: exact` on a non-judge family | reject |
| V8 | `tenant` variable that is not a `TenantProfile` field, or a `TenantProfile` field carrying free text | reject |
| V9 | Template hash ∈ revocations | build fail (FR-014b) |

## Placeholder syntax

`{{name}}` with optional inner spaces; names match `[A-Za-z_][A-Za-z0-9_]*`. Single braces are
literal (JSON examples in prompt text are safe). Rendering is done by the project's own compiler for **both** sources (research R-04). Substitution is a
single pass over the template's placeholders; runtime values are inserted verbatim and may contain `{{…}}`.
