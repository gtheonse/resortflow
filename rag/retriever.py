"""T039/T040 — Hybrid retrieval: Dense + BM25 + RRF, then cross-encoder rerank.

Architecture note on BM25: rather than persist a second BM25 vocabulary as
a Qdrant native sparse vector (which would need to be kept in lock-step
with the dense vectors on every re-index), ``BM25Retriever`` builds an
in-memory ``rank_bm25.BM25Okapi`` index per tenant by scrolling that
tenant's points straight out of Qdrant. Qdrant's payload stays the single
source of truth for both retrieval arms; the BM25 index is a derived,
cacheable-and-invalidatable view of it.

Tenant isolation (the structural guarantee plan.md calls for, not an
afterthought):
  1. ``DenseRetriever.search`` attaches a Qdrant ``tenant_id`` match filter
     to the vector query itself — a cross-tenant chunk is never a candidate.
  2. ``BM25Retriever`` builds one BM25 index *per tenant* from a
     tenant-filtered Qdrant scroll — a cross-tenant chunk never enters its
     term-frequency corpus.
  3. ``HybridRetriever.retrieve`` re-asserts the tenant_id on every fused
     payload before returning, as a defense-in-depth check on (1) and (2)
     rather than the primary mechanism.
``tenant_id`` is a required, caller-supplied argument on every retrieval
call — this module never reads it from the query text or infers it, per
AGENTS.md's "Never derive tenant_id from model output."
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qm
from rank_bm25 import BM25Okapi

from graph.models import Chunk, Citation
from rag.embeddings import EmbeddingBackend, get_embedding_backend
from rag.indexer import COLLECTION_NAME, DENSE_VECTOR_NAME, get_client

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# --- Availability-question short-circuit (AGENTS.md commitment #1:
# "Ambiguity-based routing... deterministic paths stay first-class") -------
#
# "Is there a table available tonight?" and "Is the pool view available in
# the double-queen room?" both contain the word "available" — only the first
# is a live-inventory question this corpus can never answer (plan.md: "Why
# Qdrant Payload Filtering?" section's companion note that availability is
# tool/vendor state, not retrievable knowledge). A cross-encoder relevance
# score cannot reliably tell these apart: a topically-relevant static chunk
# (e.g. a venue's overview, which legitimately mentions reservations) scores
# high on relevance even when it doesn't answer a live-availability question
# — see rag/eval/runner.py's NEGATIVE_SCORE_THRESHOLD note for the measured
# overlap. So this is deterministic pattern matching, not a relevance
# threshold: AGENTS.md's hard rule "Never put an LLM where arithmetic will
# do" applies here as much as it does to the conflict checker — an
# availability *intent* signal (an immediacy phrase co-occurring with an
# availability verb) is exactly the kind of bounded, enumerable check that
# belongs in code, not in a score cutoff or a model call (see
# rag/eval/runner.py for how this replaced an earlier score-threshold
# attempt that measurably let ~80% of negative-set cases through).
_IMMEDIATE_TIME_RE = re.compile(
    r"\b(tonight|today|tomorrow|right now|"
    r"this (friday|saturday|sunday|monday|tuesday|wednesday|thursday|weekend|week)|"
    r"next (friday|saturday|sunday|monday|tuesday|wednesday|thursday|weekend|week))\b",
    re.IGNORECASE,
)
_AVAILABILITY_VERB_RE = re.compile(
    r"\b(available|availability|open|left|book(ing)?|booked|wait(ing)?)\b", re.IGNORECASE
)
_UNAMBIGUOUS_AVAILABILITY_RE = re.compile(
    r"\b(seats left|tickets left|wait time|booked out|fully booked|same[- ]day)\b",
    re.IGNORECASE,
)


def is_availability_query(query: str) -> bool:
    """True if ``query`` is asking about live inventory/booking state (never
    in the static corpus) rather than static knowledge. An availability verb
    alone (``open``, ``available``, ``book``) is too common in legitimate
    static-content questions to use by itself — it must co-occur with an
    immediacy phrase, or match one of a handful of unambiguous phrases."""
    if _UNAMBIGUOUS_AVAILABILITY_RE.search(query):
        return True
    return bool(_AVAILABILITY_VERB_RE.search(query) and _IMMEDIATE_TIME_RE.search(query))


def payload_to_chunk(payload: dict[str, Any]) -> Chunk:
    return Chunk(
        chunk_id=payload["chunk_id"],
        tenant_id=payload["tenant_id"],
        content=payload["content"],
        source_uri=payload["source_uri"],
        valid_from=datetime.fromisoformat(payload["valid_from"]) if payload.get("valid_from") else None,
        valid_to=datetime.fromisoformat(payload["valid_to"]) if payload.get("valid_to") else None,
        content_hash=payload["content_hash"],
    )


# A ranked hit: (chunk_id, arm-specific score, full payload).
Hit = tuple[str, float, dict[str, Any]]


class DenseRetriever:
    def __init__(
        self,
        client: QdrantClient,
        embedder: EmbeddingBackend,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        self.client = client
        self.embedder = embedder
        self.collection_name = collection_name

    def search(self, tenant_id: str, query: str, top_k: int = 20) -> list[Hit]:
        if not self.client.collection_exists(self.collection_name):
            return []
        vector = self.embedder.embed_query(query)
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            using=DENSE_VECTOR_NAME,
            query_filter=qm.Filter(
                must=[qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id))]
            ),
            limit=top_k,
            with_payload=True,
        )
        return [(p.payload["chunk_id"], float(p.score), p.payload) for p in response.points]


class BM25Retriever:
    """One BM25 index per tenant, built from a tenant-filtered Qdrant
    scroll and cached in-process. Call ``invalidate()`` after a re-index."""

    def __init__(self, client: QdrantClient, collection_name: str = COLLECTION_NAME) -> None:
        self.client = client
        self.collection_name = collection_name
        self._cache: dict[str, tuple[BM25Okapi | None, list[dict[str, Any]]]] = {}

    def _load_tenant(self, tenant_id: str) -> tuple[BM25Okapi | None, list[dict[str, Any]]]:
        if tenant_id in self._cache:
            return self._cache[tenant_id]

        if not self.client.collection_exists(self.collection_name):
            self._cache[tenant_id] = (None, [])
            return self._cache[tenant_id]

        payloads: list[dict[str, Any]] = []
        offset = None
        tenant_filter = qm.Filter(
            must=[qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id))]
        )
        while True:
            batch, offset = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=tenant_filter,
                limit=256,
                with_payload=True,
                offset=offset,
            )
            payloads.extend(p.payload for p in batch)
            if offset is None:
                break

        bm25 = BM25Okapi([_tokenize(p["content"]) for p in payloads]) if payloads else None
        self._cache[tenant_id] = (bm25, payloads)
        return self._cache[tenant_id]

    def search(self, tenant_id: str, query: str, top_k: int = 20) -> list[Hit]:
        bm25, payloads = self._load_tenant(tenant_id)
        if bm25 is None:
            return []
        scores = bm25.get_scores(_tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [
            (payloads[i]["chunk_id"], float(scores[i]), payloads[i])
            for i in ranked[:top_k]
            if scores[i] > 0
        ]

    def invalidate(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(tenant_id, None)


def rrf_fuse(rankings: list[list[Hit]], k: int = 60) -> list[Hit]:
    """Reciprocal Rank Fusion across N rankings (each best-first). Score for
    an id already present is additive across rankings, per the standard RRF
    formula ``sum(1 / (k + rank))`` — no training, no arm-specific tuning."""
    scores: dict[str, float] = {}
    payloads: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        for rank, (chunk_id, _score, payload) in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
            payloads.setdefault(chunk_id, payload)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [(chunk_id, score, payloads[chunk_id]) for chunk_id, score in fused]


class CrossEncoderReranker:
    """Final quality gate: rescore RRF's top candidates with a local
    cross-encoder and reorder. ``cross-encoder/ms-marco-MiniLM-L-6-v2`` runs
    on CPU in well under the 200ms latency budget for the candidate-set
    sizes this corpus produces (~20 candidates)."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        from sentence_transformers import CrossEncoder

        from rag import _env_compat  # noqa: F401 — local-environment shim, see module docstring

        self._model = CrossEncoder(model_name)

    def rerank(self, query: str, candidates: list[Hit], top_k: int = 5) -> list[Hit]:
        if not candidates:
            return []
        pairs = [(query, payload["content"]) for _cid, _score, payload in candidates]
        scores = self._model.predict(pairs)
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [
            (candidates[i][0], float(scores[i]), candidates[i][2]) for i in order
        ]


_reranker_singleton: CrossEncoderReranker | None = None


def get_reranker() -> CrossEncoderReranker:
    global _reranker_singleton
    if _reranker_singleton is None:
        _reranker_singleton = CrossEncoderReranker()
    return _reranker_singleton


class HybridRetriever:
    """Dense + BM25 -> RRF fusion -> cross-encoder rerank -> tenant-filtered
    ``list[graph.models.Chunk]``. This return type is exactly what
    ``graph.state.dedupe_by_id`` expects, so Phase 5 can wire this
    unmodified into the supervisor graph."""

    def __init__(
        self,
        client: QdrantClient | None = None,
        embedder: EmbeddingBackend | None = None,
        reranker: CrossEncoderReranker | None = None,
        use_reranker: bool = True,
    ) -> None:
        self.client = client or get_client()
        self.embedder = embedder or get_embedding_backend()
        self.dense = DenseRetriever(self.client, self.embedder)
        self.bm25 = BM25Retriever(self.client)
        self._reranker = reranker
        self.use_reranker = use_reranker

    @property
    def reranker(self) -> CrossEncoderReranker:
        if self._reranker is None:
            self._reranker = get_reranker()
        return self._reranker

    def retrieve_hits(self, tenant_id: str, query: str, top_k: int = 5, fusion_k: int = 20) -> list[Hit]:
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for retrieval — never derive it from model output "
                "or query text (AGENTS.md hard rule)"
            )

        if is_availability_query(query):
            # Deterministic short-circuit — see is_availability_query's
            # docstring. No embedding call, no vector search, no rerank:
            # this corpus structurally cannot answer a live-inventory
            # question, so there is nothing to retrieve.
            return []

        dense_hits = self.dense.search(tenant_id, query, top_k=fusion_k)
        bm25_hits = self.bm25.search(tenant_id, query, top_k=fusion_k)
        fused = rrf_fuse([dense_hits, bm25_hits])

        # Defense in depth: both arms already filter by tenant_id at the
        # query/index level; this re-assertion is a structural backstop,
        # not the primary isolation mechanism.
        fused = [hit for hit in fused if hit[2]["tenant_id"] == tenant_id]

        if not fused:
            return []

        if self.use_reranker:
            candidate_pool = fused[: max(fusion_k, top_k * 4)]
            return self.reranker.rerank(query, candidate_pool, top_k=top_k)
        return fused[:top_k]

    def retrieve(self, tenant_id: str, query: str, top_k: int = 5, fusion_k: int = 20) -> list[Chunk]:
        hits = self.retrieve_hits(tenant_id, query, top_k=top_k, fusion_k=fusion_k)
        return [payload_to_chunk(payload) for _cid, _score, payload in hits]

    def retrieve_with_citations(
        self, tenant_id: str, query: str, top_k: int = 5
    ) -> tuple[list[Chunk], list[Citation]]:
        chunks = self.retrieve(tenant_id, query, top_k=top_k)
        citations = [
            Citation(chunk_id=c.chunk_id, source_uri=c.source_uri, quoted_span=c.content[:200])
            for c in chunks
        ]
        return chunks, citations
