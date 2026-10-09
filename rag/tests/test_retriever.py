"""T039/T040 — hybrid retrieval + cross-encoder rerank tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from graph.models import Chunk, Citation
from rag.retriever import is_availability_query, rrf_fuse
from rag.tests.conftest import requires_qdrant

EVAL_DIR = Path(__file__).parent.parent / "eval"


def test_is_availability_query_matches_all_negative_set_cases() -> None:
    data = yaml.safe_load((EVAL_DIR / "negative_set.yaml").read_text(encoding="utf-8"))
    for case in data["cases"]:
        assert is_availability_query(case["question"]), f"missed: {case['id']}: {case['question']}"


def test_is_availability_query_no_false_positives_on_eval_or_adversarial_sets() -> None:
    for fname in ("eval_set.yaml", "adversarial_set.yaml"):
        data = yaml.safe_load((EVAL_DIR / fname).read_text(encoding="utf-8"))
        for case in data["cases"]:
            assert not is_availability_query(
                case["question"]
            ), f"false positive in {fname}: {case['id']}: {case['question']}"


def test_is_availability_query_examples() -> None:
    assert is_availability_query("Is there a table available tonight?")
    assert is_availability_query("Can I book a spa slot for tomorrow?")
    assert is_availability_query("Any tickets left for the show?")
    assert not is_availability_query("What is the dress code at Silverline Chophouse?")
    assert not is_availability_query("Are connecting rooms available in the double-queen category?")


def test_rrf_fuse_combines_and_ranks_by_rank_not_raw_score() -> None:
    dense = [("a", 0.99, {"tenant_id": "MGV"}), ("b", 0.98, {"tenant_id": "MGV"})]
    bm25 = [("b", 50.0, {"tenant_id": "MGV"}), ("c", 10.0, {"tenant_id": "MGV"})]
    fused = rrf_fuse([dense, bm25])
    fused_ids = [f[0] for f in fused]
    # "b" appears rank-0 in bm25 and rank-1 in dense -> highest combined RRF.
    assert fused_ids[0] == "b"
    assert set(fused_ids) == {"a", "b", "c"}


def test_rrf_fuse_empty_rankings() -> None:
    assert rrf_fuse([[], []]) == []


@requires_qdrant
def test_retrieve_returns_chunk_instances(retriever) -> None:
    chunks = retriever.retrieve("MGV", "What is the dress code at Silverline Chophouse?", top_k=5)
    assert chunks
    assert all(isinstance(c, Chunk) for c in chunks)
    assert all(c.tenant_id == "MGV" for c in chunks)


@requires_qdrant
def test_retrieve_tenant_isolation_dense_and_bm25(retriever) -> None:
    # Query semantically and lexically overlapping with MHB content, issued
    # as MGV — no MHB chunk should ever come back.
    chunks = retriever.retrieve("MGV", "Tell me about Harbor Catch lobster rolls", top_k=10)
    assert all(c.tenant_id == "MGV" for c in chunks)

    chunks2 = retriever.retrieve("MHB", "Tell me about Silverline Chophouse dry-aged steak", top_k=10)
    assert all(c.tenant_id == "MHB" for c in chunks2)


@requires_qdrant
def test_retrieve_requires_tenant_id(retriever) -> None:
    import pytest

    with pytest.raises(ValueError):
        retriever.retrieve("", "anything", top_k=5)


@requires_qdrant
def test_retrieve_with_citations(retriever) -> None:
    chunks, citations = retriever.retrieve_with_citations(
        "MGV", "What time is check-in at Meridian Grand?", top_k=3
    )
    assert len(chunks) == len(citations)
    assert all(isinstance(c, Citation) for c in citations)
    for chunk, citation in zip(chunks, citations):
        assert citation.chunk_id == chunk.chunk_id
        assert citation.source_uri == chunk.source_uri


@requires_qdrant
def test_reranker_changes_or_preserves_top1_quality(retriever, retriever_no_rerank) -> None:
    query = "What is the minimum gaming age in Nevada?"
    reranked = retriever.retrieve("MGV", query, top_k=1)
    unranked = retriever_no_rerank.retrieve("MGV", query, top_k=1)
    assert reranked and unranked
    # Both should surface the right clause for this fairly unambiguous
    # keyword-heavy query; this is a smoke test that reranking doesn't
    # break an already-good top-1, not a strict NDCG assertion.
    assert reranked[0].chunk_id == "mgv-pol-gaming-minimum-age"


@requires_qdrant
def test_bm25_cache_invalidation(retriever) -> None:
    retriever.bm25.search("MGV", "steak")
    assert "MGV" in retriever.bm25._cache
    retriever.bm25.invalidate("MGV")
    assert "MGV" not in retriever.bm25._cache
