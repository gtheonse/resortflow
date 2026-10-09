# ADR-057 — Knowledge-answer relevance floor targets off-topic questions only

**Status**: Accepted
**Date**: 2026-09-28
**Feature**: 010-tiered-intent-routing

## Context

Today `KNOWLEDGE_QA` always answers with its three closest retrieved passages, however unrelated
— the mechanism behind defect 1 ("book flight for me to fly to India" returned a connecting-rooms
FAQ and the Nevada responsible-gaming policy pasted together). FR-021/FR-022 require a minimum
relevance level before an answer is returned. `rag/retriever.py` and `rag/eval/runner.py` already
document a prior attempt at exactly this: a plain cross-encoder score threshold, measured to let
roughly 80% of *availability*-style negatives through ("is there a table available tonight?" scores
almost as high as "what does the venue's overview say" on a topically-relevant static chunk that
simply doesn't answer a live-inventory question). That is why `is_availability_query` exists as a
deterministic short-circuit rather than a score cutoff. Any new floor risks repeating that exact
mistake if it is not scoped to the class of question the earlier attempt actually failed on.

## Options Considered

**1. One relevance floor covering both availability and off-topic negatives**, replacing
`is_availability_query`'s short-circuit. Simpler — one mechanism, one calibration pass. Rejected:
this is the option already measured to fail. A cross-encoder score cannot reliably separate "cabana
available Saturday?" from "what does the Meridian Pool Club include?" because the retrieved content
genuinely is topically relevant to both; no floor value threads that needle without either refusing
real availability-adjacent FAQ answers or letting availability negatives back through.

**2. An LLM relevance judge on every knowledge answer.** Would generalize past pattern matching.
Rejected under Principle I and AGENTS.md's "never put an LLM where arithmetic will do": this is a
deterministic path today, and adding a model call to every FAQ turn trades latency and cost for a
capability the existing deterministic short-circuit plus a scoped score floor already covers.

**3. A relevance floor scoped to off-topic questions only, calibrated against a labelled
off-topic set, leaving `is_availability_query`'s short-circuit untouched** (the chosen shape).
Off-topic content ("weather in Delhi", "book a flight") is not merely a live-inventory question the
static corpus structurally cannot answer — it is not topically related to the corpus at all, which
is exactly the case a cross-encoder score *can* separate cleanly, unlike the availability case.

## Decision

**Option 3.** `KNOWLEDGE_QA` switches from `retrieve_with_citations` to the existing public
`HybridRetriever.retrieve_hits`, which already returns the rerank score — no change to `rag/`
retrieval logic itself. A new floor, `knowledge_relevance_floor` (`graph/routing/thresholds.yaml`,
owned by Track C, calibrated by `rag/eval/calibrate_floor.py`, which only prints the value it finds
rather than writing Track D's files), gates the answer: below it, the turn falls through to
`CLARIFY`, exactly as a genuine retrieval miss already does. `is_availability_query`'s deterministic
short-circuit is unchanged and unconsulted by this floor — an availability question never reaches
the score comparison at all, so the floor's calibration set (`rag/eval/negative_set.yaml`'s new
`offtopic:` section, disjoint from the existing availability `cases:` section) never needs to solve
the problem already known to be unsolvable this way.

## Consequences

**Made easier.** The floor's calibration (`rag/eval/calibrate_floor.py`, sweeping `eval_set.yaml`
against the new `offtopic` cases) has a realistic chance of finding a value that refuses 100% of
what it targets while keeping 100% of answerable questions (SC-008) — because, unlike the
availability case, the two score distributions are not expected to overlap heavily to begin with.

**Made harder.** Off-topic and availability negatives now have two different defence mechanisms
(a deterministic phrase/verb short-circuit, and a calibrated score floor) rather than one. A future
engineer adding a new negative-case category has to decide, deliberately, which mechanism it
belongs under — this ADR's own distinction (does the content structurally not answer the question,
or is it not topically related at all) is the test to apply, and it is not always obvious on first
read which side a new case falls on.

**Foreclosed.** No future change may fold the availability short-circuit into the relevance-floor
comparison "for consistency" without re-measuring against `rag/eval/negative_set.yaml`'s existing
`cases:` section first — that measurement is exactly what this ADR is built on, and undoing the
separation without re-checking it would silently reopen the ~80% pass-through failure this ADR's
Context section describes.
