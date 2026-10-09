"""Prompt-injection screening (ADR-032).

`screen_retrieved_content` is a deterministic corpus/embedding-similarity
screen over retrieved chunks — no LLM call participates in the decision. A
chunk flagged `suspect` is excluded from whatever generation context it
would otherwise enter.

Independent structural guarantee (defense-in-depth, not just screening
accuracy): retrieved-content strings are never interpolated into any prompt
with tool-calling ability — verified by
`agents/tests/test_injection_structural.py`, not by this module. Tool
selection is driven by intent-parser output and state, never by retrieved
document text, so an injected instruction has no channel to reach a tool
call regardless of this screen's recall (FR-008).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from graph.models import Chunk
from policy._similarity import best_match
from policy.types import InjectionScreeningResult

_CORPUS_PATH = Path(__file__).parent.parent / "content" / "redteam" / "injection_corpus.yaml"


def load_injection_corpus(path: Path = _CORPUS_PATH) -> dict[str, str]:
    """Load `content/redteam/injection_corpus.yaml` into the `{pattern_id:
    text}` shape `screen_retrieved_content` expects."""
    data = yaml.safe_load(path.read_text())
    return {entry["id"]: entry["text"] for entry in data["patterns"]}

SUSPECT_THRESHOLD = 0.72
"""Similarity at or above this value against a known injection pattern
marks a chunk `suspect`. Same threshold value as `policy.distress`'s
`CONFIDENT_THRESHOLD` — both screens share the technique (ADR-032/036); no
ambiguous band here, since a chunk is excluded from a generation context
outright rather than routed to a human (unlike distress, false positives
here just mean a slightly less complete answer, not an under-reaction to a
safety signal — asymmetric costs justify the different response, not a
different threshold value)."""


def screen_retrieved_content(
    chunks: list[Chunk], *, corpus: dict[str, str]
) -> list[InjectionScreeningResult]:
    """One result per input chunk, same order. Pure function of its
    inputs — no network call, no LLM."""
    results: list[InjectionScreeningResult] = []
    for chunk in chunks:
        pattern_id, score = best_match(chunk.content, corpus)
        if score >= SUSPECT_THRESHOLD:
            results.append(
                InjectionScreeningResult(
                    chunk_id=chunk.chunk_id, verdict="suspect",
                    matched_pattern_id=pattern_id, similarity_score=score,
                )
            )
        else:
            results.append(
                InjectionScreeningResult(
                    chunk_id=chunk.chunk_id, verdict="clean",
                    matched_pattern_id=None, similarity_score=score,
                )
            )
    return results
