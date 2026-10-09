# ADR-032 — Prompt-injection screening: corpus/embedding match plus a structural tool-call-isolation guarantee

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

This phase's roster names "prompt-injection screening; injection corpus in RAG content
as a red-team test," and spec.md's User Story 3/FR-007/FR-008 require that content
retrieved from external or editable sources (property knowledge documents) can never
alter which tool the system calls, what parameters it uses, or whether an approval gate
is bypassed — an absolute requirement ("MUST NOT be able to"), not a best-effort one.
`rag/eval/adversarial_set.yaml` already establishes a precedent for adversarial-test
fixtures living alongside the retrieval eval suite (200 cross-tenant probes, feature
002); no equivalent injection corpus exists yet. `sentence-transformers` is already a
project dependency (`pyproject.toml`, used by `rag.HybridRetriever`); no PII/redaction-
style new ML dependency is needed for this screening layer.

## Options Considered

**1. An LLM-based injection classifier** (ask a model whether a chunk contains an
injection attempt). Rejected: this both re-introduces a second model into the exact
attack surface being screened for (a classifier prompt is itself attackable) and cannot
supply FR-008's absolute guarantee — a probabilistic classifier can miss a novel
phrasing, and "the tool-call-integrity guarantee holds only when the classifier is
right" is not the guarantee the spec asks for.

**2. Deterministic corpus/embedding-similarity screening only**, with no structural
backstop — trust the screening layer to catch what it catches. Cheaper to build.
Rejected alone: screening is necessarily incomplete against phrasings not yet seen, and
FR-008 does not say "usually" — an absolute guarantee needs an architectural property,
not just a better filter.

**3. Deterministic corpus/embedding-similarity screening (excludes suspect chunks from
the generation context) *plus* a structural, tested guarantee that retrieved-content
strings are never interpolated into any prompt with tool-calling ability** — i.e., the
screening layer improves what the guest sees, while the tool-call-integrity guarantee
comes from the existing architecture (intent parsing and planning already run off guest
text and state, never off retrieved document content) made explicit and verified by a
test, not merely assumed.

## Decision

**Option 3.** `policy/injection.py:screen_retrieved_content` runs (a) exact/fuzzy match
against a maintained corpus (`content/redteam/injection_corpus.yaml`, sibling to
`rag/eval/adversarial_set.yaml`) and (b) embedding-similarity comparison via the existing
`sentence-transformers` dependency — no new ML dependency. A chunk flagged `suspect` is
excluded from the context passed to answer generation; a `clean` chunk is used normally.
Independently, a new structural test (`test_retrieved_content_never_feeds_tool_selection`,
mirroring `agents/gaming/tests/test_graph.py`'s existing "no mutation verb" structural-
test style) asserts that no intent-parsing or tool-dispatch code path in any domain reads
from `state["retrieved"]`/`Chunk` content — verifying, not merely assuming, that the
architecture already gives FR-008 its absolute guarantee regardless of screening recall.

## Consequences

**Made easier.** The guest still gets a useful answer when only some retrieved content is
suspect (clean chunks remain available) rather than the whole turn failing. FR-008's
guarantee is provable by a test that runs in CI, not an architectural claim that could
silently rot as domains are added.

**Made harder.** Every new domain that adds a retrieval call site must be covered by the
structural test above, or the guarantee has a silent gap — an ongoing discipline, not a
one-time cost.

**Foreclosed.** No future feature may make retrieved-content strings participate in
tool-selection or planning prompts without deliberately revisiting this ADR — the
structural test exists specifically to catch that regression before it ships, not to be
deleted when inconvenient.
