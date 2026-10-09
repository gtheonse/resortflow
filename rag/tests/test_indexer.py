"""T038 — Qdrant indexing tests. Requires a running Qdrant (see conftest)."""

from __future__ import annotations

from content.chunker import chunk_all
from rag.indexer import COLLECTION_NAME
from rag.tests.conftest import requires_qdrant


@requires_qdrant
def test_collection_created(indexed_client) -> None:
    assert indexed_client.collection_exists(COLLECTION_NAME)


@requires_qdrant
def test_point_count_matches_chunk_count(indexed_client) -> None:
    expected = len(chunk_all())
    info = indexed_client.get_collection(COLLECTION_NAME)
    assert info.points_count == expected


@requires_qdrant
def test_payload_carries_tenant_id(indexed_client) -> None:
    points, _ = indexed_client.scroll(COLLECTION_NAME, limit=10, with_payload=True)
    assert points
    for p in points:
        assert p.payload["tenant_id"] in ("MGV", "MHB")
        assert "chunk_id" in p.payload
        assert "content" in p.payload


@requires_qdrant
def test_reindex_is_idempotent_on_count(indexed_client, embedder) -> None:
    from rag.indexer import build_index

    n1 = build_index(client=indexed_client, embedder=embedder, recreate=True)
    n2 = build_index(client=indexed_client, embedder=embedder, recreate=True)
    assert n1 == n2
