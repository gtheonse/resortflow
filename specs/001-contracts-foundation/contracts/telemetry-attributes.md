# Interface Contract: Telemetry Attribute Vocabulary

**Feature**: `001-contracts-foundation` | Permanent home: `observability/attributes.py`

Freezes the attribute *names* only. Emission (the tracer, the exporter, the dual-backend
callback handlers) is feature 007's responsibility. This document — and the module it
becomes — is the single place any attribute name may be defined, per the constitution's
OBSERVABILITY IS NOT OPTIONAL standard.

**Why now, not in feature 007**: Track B emits tool spans in feature 002, five features
before observability is built. Without a frozen vocabulary, three tracks invent three sets
of names and feature 007 becomes a renaming pass across every saved dashboard and eval
query. See `research.md` item 8.

## The five identifiers

| Identifier | Scope | Lifetime | Cardinality rule |
|---|---|---|---|
| `session_id` | One guest conversation | Minutes–days | Span attribute only — unbounded |
| `trace_id` | One turn | Seconds | Span attribute only |
| `span_id` | One operation | Milliseconds | Span attribute only |
| `saga_id` | One multi-system transaction | **Spans multiple turns** | Span attribute only |
| `idempotency_key` | One mutation attempt | Single call | Span attribute only |

`saga_id` deliberately outlives `trace_id` — a hold placed in turn 3, approved in turn 5,
compensated in turn 6, is three traces and one saga. Parent-child span relationships cannot
express that; the correlation is carried as a queryable attribute plus OTel span links
(feature 007 concern).

## Cardinality discipline

This is a contract, not a suggestion — putting an unbounded identifier on a metric is how a
five-figure Log Analytics bill happens.

- **Span attributes only, never metric dimensions**: `session_id`, `guest_id`, `saga_id`.
- **Safe as metric dimensions** (bounded): `tenant_id`, `node`, `domain`, `model`,
  `outcome`.

**The `node` dimension is bounded by**: `graph/nodes.py`, the node-name registry this
feature freezes. Every workflow-step identity emitted in production must resolve to a
string constant declared there; no step may introduce a new node name unilaterally.
Verification: a test asserts that no `node` attribute is set to a value missing from that
registry.

## Namespaces

Every span that represents a model or tool operation carries attributes in **both**
namespaces simultaneously, so the same instrumentation is portable between the local
Langfuse backend and the production Foundry/Azure Monitor backend:

- `gen_ai.*` — Foundry / GenAI semantic conventions (`gen_ai.operation.name`,
  `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, …).
- `langfuse.*` — Langfuse's own namespace (`langfuse.observation.type`,
  `langfuse.trace.metadata.node`, `langfuse.session.id`, …).
- `resortflow.*` — project-specific, queryable in both backends (`resortflow.node`, `resortflow.domain`,
  `resortflow.tenant_id`, `resortflow.saga_id`, `resortflow.graph_version`).

## What this feature freezes vs. defers

| Frozen now (this feature) | Deferred (feature 007) |
|---|---|
| The five identifier names and their cardinality class | The tracer / exporter selection |
| The three namespace prefixes | PII masking implementation |
| The rule that no attribute name may be defined outside this module | Payload externalization to Blob Storage |
| — | Sampling strategy |

A test in feature 007 (not this one) will assert that no attribute-name string literal
appears outside `observability/attributes.py`. This feature's obligation is only to make
that assertion possible by giving the module something to check against.

---

## Amendment — Feature 009 (prompt lineage)

**Approved for feature `009-prompt-management` (blocker B-1); the vocabulary above is otherwise unchanged.**
Exactly five span attributes are added to `observability/attributes.py`, all in the `resortflow.` namespace and
emitted only by `observability.telemetry.start_generation_span`:

| Constant | Attribute | Type | When |
|---|---|---|---|
| `PROMPT_NAME` | `resortflow.prompt.name` | string | every in-scope model call |
| `PROMPT_VERSION` | `resortflow.prompt.version` | string | every call (`git-<12 hex>` for Git-sourced, the registry integer as text for `langfuse`) |
| `PROMPT_SHA256` | `resortflow.prompt.sha256` | string | every call |
| `PROMPT_SOURCE` | `resortflow.prompt.source` | enum string | every call — `git`, `langfuse`, `git-fallback`, `git-fallback-unapproved`, `git-fallback-revoked` |
| `PROMPT_REJECTED_REASON` | `resortflow.prompt.rejected_reason` | enum string | only when a registry version was rejected |

**Cardinality rule.** `resortflow.prompt.sha256` and `resortflow.prompt.version` are **span attributes only — never metric
dimensions** (unbounded over time), so they are not added to `BOUNDED_METRIC_DIMENSIONS`. `resortflow.prompt.name`
(6 values) and `resortflow.prompt.source` (5 values) are bounded and *may* become metric dimensions later; that is
not proposed here.

**Not added.** No `langfuse.observation.prompt.*` attribute is defined or emitted (ADR-052 D5, option ii):
lineage is `resortflow.*` only, and no claim is made that the Langfuse UI links a generation to its prompt version.

Source of truth for the details: `specs/009-prompt-management/contracts/telemetry-prompt-attributes.md`.

---

## Amendment — Feature 010 (routing decision)

**Approved for feature `010-tiered-intent-routing` (IC-010, the feature's single integration
commit); the vocabulary above is otherwise unchanged.** Seven span attributes are added to
`observability/attributes.py`, all in the `resortflow.` namespace, emitted only by
`observability.telemetry.annotate_routing` on the `intent_router` span, once per turn:

| Constant | Attribute | Type | Cardinality |
|---|---|---|---|
| `ROUTING_KIND` | `resortflow.routing.kind` | enum string | 7 values (`book`, `lookup`, `modify`, `faq`, `out_of_scope`, `smalltalk`, `undecided`) |
| `ROUTING_SERVICES` | `resortflow.routing.services` | comma-joined, sorted string | not bounded in combination — NOT a metric dimension |
| `ROUTING_TIER` | `resortflow.routing.tier` | int | 3 values (0, 1, 2) |
| `ROUTING_CONFIDENCE` | `resortflow.routing.confidence` | float | unbounded — NOT a metric dimension |
| `ROUTING_AMBIGUOUS` | `resortflow.routing.ambiguous` | bool | 2 values |
| `ROUTING_CARRIED_OVER` | `resortflow.routing.carried_over` | bool | 2 values |
| `ROUTING_TIER2_OUTCOME` | `resortflow.routing.tier2_outcome` | enum string | 5 values (`not_called`, `decided`, `invalid`, `timeout`, `unavailable`) |

**Cardinality rule.** `kind`, `tier` and `tier2_outcome` are added to `BOUNDED_METRIC_DIMENSIONS`.
`services` and `confidence` are span attributes only, for the same reason `resortflow.prompt.sha256` is:
`services` combinations and `confidence` values are effectively unbounded over time.

**Never guest text.** No `resortflow.routing.*` attribute carries the guest's message, and none is
derived from anything the guest wrote beyond the decision's own bounded, code-produced fields
(FR-029).

Source of truth for the details: `specs/010-tiered-intent-routing/contracts/router-api.md` and
`data-model.md`.

## Amendment — Feature 011

`resortflow.routing.kind` gains the bounded value `browse` (feature 011, ADR-059): the guest asked to see what is
available (venues, events, treatments, rooms, the resort's experiences). No new attribute is added and no
attribute carries guest text; `kind` was already a bounded metric dimension, so the value set simply grows by
one. Raised here rather than edited unilaterally, per AGENTS.md's observability-contract rule.
