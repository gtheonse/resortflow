"""T037/T041 — internal-only Track D types.

``graph.models.Chunk`` and ``graph.models.Citation`` are frozen contracts
from feature 001 (see specs/003-grounding-retrieval/plan.md's "CRITICAL:
Reuse frozen models" section) and MUST NOT be redefined here. Everything in
this module is internal to the retrieval pipeline and never crosses into
``ResortFlowState`` — it exists only to move data between ``content/chunker.py``,
``rag/indexer.py``, ``rag/retriever.py``, and ``rag/eval/runner.py``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RawChunk(BaseModel):
    """A pre-embedding chunk, produced by ``content/chunker.py`` before
    ``content_hash`` and final ``graph.models.Chunk`` assembly. Carries a
    couple of indexing-only fields (``domain``, ``tenant_slug`` for the
    corpus directory) that don't belong on the frozen ``Chunk`` contract."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    domain: str
    source_uri: str
    content: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    seq: int = 0
    """Position within its source document — used to build a stable
    ``chunk_id`` (``{source_uri}#{seq}``)."""


class RetrievedChunk(BaseModel):
    """A chunk plus the scores it accumulated along the retrieval pipeline
    (dense rank, BM25 rank, RRF score, cross-encoder score). Not part of
    ``ResortFlowState`` — the retriever's public return type stays
    ``list[graph.models.Chunk]`` per the plan; this is for eval/debugging
    and for ``rag/retriever.py`` internals."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    chunk_id: str
    tenant_id: str
    content: str
    source_uri: str
    dense_rank: int | None = None
    bm25_rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None


class EvalCase(BaseModel):
    """One row of ``rag/eval/eval_set.yaml``, ``adversarial_set.yaml``, or
    ``negative_set.yaml``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    domain: str | None = None
    question: str
    gold_chunk_ids: list[str] = Field(default_factory=list)
    """Empty for negative-set cases: those must retrieve nothing."""
    query_as_tenant_id: str | None = None
    """Adversarial-set only: the tenant identity the query is issued as,
    when different from ``tenant_id`` (the tenant whose content is being
    probed for)."""
    kind: str = "eval"
    """One of ``eval`` / ``adversarial`` / ``negative`` — which set this
    case came from, so the runner can apply the right assertion."""


class RetrievalMetrics(BaseModel):
    """Aggregate RAGAS-style metrics computed by ``rag/eval/runner.py``."""

    model_config = ConfigDict(extra="forbid")

    context_recall: float
    context_precision: float
    num_cases: int
    per_case_recall: dict[str, float] = Field(default_factory=dict)


class AdversarialResult(BaseModel):
    """Outcome of one cross-tenant probe."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    leaked_chunk_ids: list[str] = Field(default_factory=list)
    passed: bool


class NegativeResult(BaseModel):
    """Outcome of one negative (availability-question) probe."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    returned_chunk_ids: list[str] = Field(default_factory=list)
    passed: bool
