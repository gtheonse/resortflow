# Contract: Prompt lineage attributes (amends `specs/001-contracts-foundation/contracts/telemetry-attributes.md`)

> **Decided 2026-09-26 (ADR-052 D5, option ii).** Lineage is emitted as `resortflow.prompt.*` attributes
> **only**. No `langfuse.observation.prompt.*` attribute is emitted, no propagation span processor
> is added, and this feature makes **no claim** that the Langfuse UI links a generation to its
> prompt version. `observability/attributes.py` is the frozen Phase-0 vocabulary, so the five
> constants below are a contract change approved for this feature (blocker B-1, resolved). All
> names are defined only in `observability/attributes.py` (constitution: OBSERVABILITY IS NOT
> OPTIONAL), and the 001 contract file is amended when they land.

## New constants (five)

| Constant | Key | Type | When emitted |
|---|---|---|---|
| `PROMPT_NAME` | `resortflow.prompt.name` | string | every in-scope model call |
| `PROMPT_VERSION` | `resortflow.prompt.version` | string | every call (`git-<12 hex>` for Git-sourced, the registry integer as text for `langfuse`) |
| `PROMPT_SHA256` | `resortflow.prompt.sha256` | string | every call |
| `PROMPT_SOURCE` | `resortflow.prompt.source` | enum string | every call — five values: `git`, `langfuse`, `git-fallback`, `git-fallback-unapproved`, `git-fallback-revoked` |
| `PROMPT_REJECTED_REASON` | `resortflow.prompt.rejected_reason` | enum string | only when a registry version was rejected |

## Why not Langfuse's own prompt-link keys

Langfuse's OTel mapping table lists `langfuse.observation.prompt.name` / `.version`, and SDK
4.15.2 sets them only for non-fallback prompts. The docs, however, do not state that raw-OTel
linking renders in the UI, and the link would bind to our wrapper span rather than to the
instrumented model call that carries `gen_ai.usage.*`. Rather than freeze two more names on an
unverified behaviour, they are not emitted. **Accepted cost:** prompt identity (on the wrapper
generation span) and token cost (on the instrumented child span) are correlated through the parent
span, not shown together in the Langfuse prompt view. Langfuse also documents a
`langfuse.observation.metadata.*` mapping that could surface prompt identity as searchable
metadata; it is not adopted now because it would add attribute names.

## Cardinality

`resortflow.prompt.sha256` and `resortflow.prompt.version` are **span attributes only — never metric
dimensions** (unbounded over time). `resortflow.prompt.name` (6 values) and `resortflow.prompt.source`
(5 values) are bounded and MAY be metric dimensions; add them to `BOUNDED_METRIC_DIMENSIONS` only
if a metric needs them (not proposed here).

## Emission

| Function (in `observability/telemetry.py`) | Contract |
|---|---|
| `start_generation_span(name, *, prompt: ResolvedPrompt, model: str, node: str, domain: str \| None = None, session_id: str \| None = None)` | Like `start_agent_span`, but sets `langfuse.observation.type="generation"`, the model, and the applicable `resortflow.prompt.*` attributes; the only path that sets them |

Prompt **text** is never an attribute here. Any prompt input/output payload still goes through
`set_payload` and its PII redaction; guest text is redacted before it reaches a span.

## Acceptance (automated, no Langfuse server required)

1. A call resolved from Git emits all four always-present attributes and no rejected-reason.
2. A call that fell back after a rejected registry version emits `rejected_reason` and the
   matching `git-fallback*` source.
3. No `langfuse.observation.prompt.*` attribute ever appears on any span (regression test).
4. `resortflow.prompt.sha256` is never registered as a metric dimension.
5. A `Langfuse(tracing_enabled=False)` client constructed in the same process does not replace
   the global tracer provider, attach a span processor, or change the exported span set (FR-031).
6. A judge call through `OpenAICompatibleJudgeClient` emits the same four always-present attributes (SC-002); `prompt` is a required argument of `JudgeClient.complete`.
