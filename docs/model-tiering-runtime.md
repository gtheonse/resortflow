# Model tiering — runtime

**Scope: this file governs which Azure OpenAI model the *product* calls at inference time**,
serving a real guest conversation or a real eval run. It says nothing about which Claude model
is used in Claude Code while executing a spec-kit task — that is `docs/models.md`'s "build-time"
table, a deliberately separate vocabulary (see that file's own note on why the two must not be
conflated). The reasoning behind the runtime policy is in `docs/ResortFlow-Model-Tiering.md`.

Every row here is a **static** declaration made at design time, in code, per node — never a
runtime decision the graph makes dynamically. Constitution Principle VI ("Declared Model Tier")
requires this: a node's tier is a fact you can read off the source, not something that varies by
conversation.

## Tier map

| Tier | Env var (deployment name default) | Determinism |
|---|---|---|
| Economy | `MODEL_CHEAP` (`gpt-4.1-mini`) | temperature 0 / deterministic settings, no retry, no escalation |
| Frontier | `MODEL_JUDGE` (`gpt-4.1`) | used only for judge/planner/recovery roles, never for a guest-facing extraction or classification node |

## Runtime-tiered nodes (one row per model-calling node in the codebase)

| Node / module | Tier | Deployment | Feature | Notes |
|---|---|---|---|---|
| `graph/routing/classifier.py::AzureRoutingClassifier` (`intent_router`, tier 2) | Economy | `MODEL_CHEAP` | 010 — Tiered Intent Routing | Only called below `HIGH_CONFIDENCE`, never for `smalltalk`/`out_of_scope` (FR-009); 1.5s hard timeout, no retry, no escalation to a stronger model on failure — every failure degrades to the existing clarifying question (`UnavailableClassifier`), never a silent frontier fallback. |
| `agents/dining/nodes/intent_parser.py::AzureIntentExtractor` (dining intent extraction) | Economy | `MODEL_CHEAP` | pre-010 (dining domain) | Same economy-tier, deterministic-settings pattern `AzureRoutingClassifier` followed; a `RuleBasedIntentExtractor` fallback exists with no model call at all. |
| `eval/judge.py::JudgeClient` (retrieval/trajectory judges) | Frontier | `MODEL_JUDGE` | 007 — Evaluation & Observability | Judge role only — never in a guest-facing turn; every call carries prompt lineage (`prompts.registry`, feature 009). |

No node in the codebase currently escalates from economy to frontier at runtime, and none is
planned to — every economy-tier node's failure mode is a deterministic fallback (clarify, rule-
based extraction), not a costlier model call. If a future node needs escalation, it must be
declared statically here as its own row, not inferred from a retry loop.

## Adding a row

A new runtime-tiered node's PR should add its row here in the same commit that adds the node,
mirroring `graph/routing/classifier.py`'s own docstring declaration.

## Note — feature 011 (2026-09-30)

`graph/routing/classifier.py::AzureRoutingClassifier` (tier 2 of `intent_router`) keeps its tier: **economy
(`MODEL_CHEAP`), static, no escalation**. Its prompt (`prompts/templates/intent_routing_classification.yaml`)
was revised so the closed kind set includes `browse` and enumeration requests are never `out_of_scope`. The
browse path itself makes **no** model call (deterministic tier 1 + provider reads), so this file gains no new
row.
