# ADR-043 — Hand-rolled, pinned-judge scorers over a third-party eval library

**Status**: Accepted
**Date**: 2026-09-13
**Feature**: 007-eval-observability-gate

## Context

FR-001/002 require RAGAS-style retrieval metrics (context recall, context
precision, faithfulness, answer relevance) and agent-trajectory metrics
(tool selection, plan quality, unnecessary-step rate). FR-009/010 additionally
require every metric to name a specific, pinned judge model, and that judge
model to be provably no weaker than the model it judges. `pyproject.toml`
currently has no eval-specific library (`ragas`, `deepeval`, or similar) — this
is a genuine build-vs-adopt decision, not a continuation of an existing choice.

## Options Considered

**1. Adopt the `ragas` library** for the four retrieval metrics. Advantage:
purpose-built, widely used, saves writing four judge prompts from scratch.
Disadvantage: `ragas` manages its own judge-model wiring internally (typically
through a LangChain LLM wrapper); reconciling that internal judge selection
with this feature's requirement that every metric name a specific, pinned
model version — and that the judge be verified no weaker than the generator —
means either fighting the library's abstraction or wrapping it so thoroughly
that little of its convenience remains. It would also be the first
eval-specific dependency this codebase has ever taken on.

**2. Hand-rolled scorers**, calling the same pinned judge model client
directly for the four retrieval-quality prompts, with agent-trajectory
correctness (tool selection, unnecessary-step rate) computed deterministically
by diffing the actual tool-call sequence against a reference sequence, and the
judge model reserved only for "plan quality" cases with more than one valid
reference path. Advantage: judge-model identity and version are first-class,
directly satisfying FR-009/010 without an adapter layer; keeps determinism
wherever a reference answer exists, per Constitution Principle I. Disadvantage:
four prompts to write, test, and maintain in-house instead of importing them.

**3. LLM-judge everything**, including trajectory correctness, for
implementation uniformity. Advantage: one code path for all scoring.
Disadvantage: directly violates Constitution Principle I (DETERMINISM FIRST) —
tool-selection correctness has a ground-truth reference sequence to diff
against mechanically; using a judge model for it trades a 100%-reliable
2ms check for a probabilistic one for no benefit.

## Decision

**Adopt Option 2.** Retrieval-quality metrics are hand-rolled prompts against
the project's existing pinned-judge-model client pattern; trajectory
correctness is deterministic sequence comparison; only genuinely ambiguous
"plan quality" cases use the judge model.

## Consequences

**Made easier.** Judge-model pinning and the "judge not weaker than generator"
guarantee (FR-009/010) are structural, not bolted onto a third-party
abstraction. No new runtime dependency's release cycle or breaking changes to
track.

**Made harder.** The four retrieval-quality prompts must be written, reviewed,
and iterated on in-house rather than inheriting a maintained library's prompt
engineering; this is genuine, ongoing authorship work this feature and its
successors own.

**Foreclosed.** This feature does not get `ragas`'s broader metric catalogue
"for free" if a future phase wants a metric beyond the four named here — adding
one means writing another prompt, not flipping a config flag in an adopted
library.
