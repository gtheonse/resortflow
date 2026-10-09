"""Shared corpus/embedding-similarity technique (ADR-032, ADR-036).

Private to `policy/` — not part of the public contract
(`contracts/policy-engine-contract.md`). `policy/injection.py` and
`policy/distress.py` both call `best_match` against their own corpus and
thresholds; this module exists so the two share one implementation of "how
to score a piece of text against a maintained corpus," not two.

Local `sentence-transformers` model only — deliberately NOT
`rag.embeddings.get_embedding_backend()`, which prefers Azure OpenAI when
configured. A policy-critical screen must stay fully offline and
deterministic regardless of which embedding provider the RAG pipeline
happens to be configured with; reusing that backend would let an
environment variable silently change this screen's behavior.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np


@lru_cache(maxsize=1)
def _get_model() -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


@lru_cache(maxsize=8)
def _corpus_embeddings(corpus_items: tuple[tuple[str, str], ...]) -> tuple[tuple[str, ...], np.ndarray]:
    """Corpus embeddings, computed once per distinct corpus and cached —
    `best_match` is called on every conversational turn (`check_rg_status`-
    adjacent hot path, constitution: "before any gaming-domain tool
    executes... on every turn"), so re-embedding an entire static corpus on
    every single call is a real performance defect, not just a slow test:
    an uncached call against a 10-entry corpus took ~25s end-to-end in a
    full graph invocation during this feature's own implementation. `corpus_items` is a hashable
    `tuple(sorted(corpus.items()))`, not the dict itself, since `lru_cache`
    requires hashable arguments; `DISTRESS_CORPUS`/the loaded injection
    corpus are both static module-level data, so this cache never goes
    stale within a process lifetime."""
    ids = tuple(pattern_id for pattern_id, _ in corpus_items)
    texts = [text for _, text in corpus_items]
    model = _get_model()
    embeddings = model.encode(texts, normalize_embeddings=True)
    return ids, embeddings


def best_match(text: str, corpus: dict[str, str]) -> tuple[str | None, float]:
    """Score `text` against every entry in `corpus` (id -> example text).

    Two layers, per ADR-032/036:
    1. Exact/fuzzy substring match — either string containing the other
       (case-insensitive) scores a perfect 1.0 match immediately, no model
       call needed.
    2. Embedding cosine similarity against every corpus entry otherwise —
       catches paraphrases a substring check would miss. Corpus embeddings
       are cached (`_corpus_embeddings`); only the query text is embedded
       fresh on each call.

    Returns `(None, 0.0)` for an empty corpus. Otherwise always returns the
    single best-scoring entry's id and its similarity score.
    """
    if not corpus:
        return None, 0.0

    normalized = text.strip().lower()
    for pattern_id, pattern in corpus.items():
        pattern_normalized = pattern.strip().lower()
        if pattern_normalized and (
            pattern_normalized in normalized or normalized in pattern_normalized
        ):
            return pattern_id, 1.0

    ids, corpus_embeddings = _corpus_embeddings(tuple(sorted(corpus.items())))
    model = _get_model()
    query_embedding = model.encode([text], normalize_embeddings=True)[0]
    scores = corpus_embeddings @ query_embedding
    best_idx = int(np.argmax(scores))
    return ids[best_idx], float(scores[best_idx])
