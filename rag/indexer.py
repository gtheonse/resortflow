"""T038 — Qdrant collection setup and indexing.

One collection, one named dense vector (``dense``). BM25 is deliberately
*not* stored as a Qdrant native sparse vector here — see rag/retriever.py's
module docstring for why: it runs in-process via ``rank_bm25`` against the
tenant-scoped content Qdrant itself returns, so there is exactly one
source of truth (Qdrant's payload) for both retrieval arms rather than a
second persisted BM25 vocabulary that could drift out of sync with it.

Tenant isolation (plan.md's "Why Qdrant Payload Filtering?"): every point's
payload carries ``tenant_id``, and every query path in ``rag/retriever.py``
attaches a ``tenant_id`` match filter. This module does not enforce that —
it only puts ``tenant_id`` on every payload so there is something to filter
on.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from content.chunker import chunk_all
from graph.models import Chunk
from rag.embeddings import EmbeddingBackend, get_embedding_backend

COLLECTION_NAME = "resortflow_chunks"
DENSE_VECTOR_NAME = "dense"


def get_client(url: str | None = None) -> QdrantClient:
    return QdrantClient(url=url or os.environ.get("QDRANT_URL", "http://localhost:6333"))


def ensure_collection(
    client: QdrantClient,
    *,
    dense_size: int,
    collection_name: str = COLLECTION_NAME,
    recreate: bool = False,
) -> None:
    """Create the collection if it doesn't exist (or always, if ``recreate``).

    Payload indexes on ``tenant_id`` and ``domain`` are created explicitly —
    Qdrant can filter on any payload field without one, but an index makes
    the tenant filter (applied to *every* query, per the tenant-isolation
    design) fast rather than a full scan as the corpus grows.
    """
    exists = client.collection_exists(collection_name)
    if exists and recreate:
        client.delete_collection(collection_name)
        exists = False

    if not exists:
        client.create_collection(
            collection_name=collection_name,
            vectors_config={
                DENSE_VECTOR_NAME: qm.VectorParams(
                    size=dense_size, distance=qm.Distance.COSINE
                ),
            },
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="tenant_id",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=collection_name,
            field_name="domain",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )


def _stable_point_id(chunk_id: str) -> str:
    """Qdrant point IDs must be a UUID or unsigned int; chunk_id is a
    human-readable slug, so derive a deterministic UUID5 from it. The
    original chunk_id is preserved verbatim in the payload."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"resortflow-chunk:{chunk_id}"))


def chunk_to_payload(chunk: Chunk, domain: str) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "tenant_id": chunk.tenant_id,
        "domain": domain,
        "content": chunk.content,
        "source_uri": chunk.source_uri,
        "valid_from": chunk.valid_from.isoformat() if chunk.valid_from else None,
        "valid_to": chunk.valid_to.isoformat() if chunk.valid_to else None,
        "content_hash": chunk.content_hash,
    }


def _domain_from_source_uri(source_uri: str) -> str:
    # "content/corpus/mgv/dining.yaml#..." -> "dining"
    path_part = source_uri.split("#", 1)[0]
    filename = path_part.rsplit("/", 1)[-1]
    return filename.removesuffix(".yaml")


def index_chunks(
    client: QdrantClient,
    chunks: Iterable[Chunk],
    embedder: EmbeddingBackend,
    *,
    collection_name: str = COLLECTION_NAME,
    batch_size: int = 64,
) -> int:
    """Upsert chunks into Qdrant with a dense vector and a tenant-bearing
    payload. Returns the number of points upserted."""
    chunks = list(chunks)
    if not chunks:
        return 0

    ensure_collection(client, dense_size=embedder.dimension, collection_name=collection_name)

    total = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        dense_vecs = embedder.embed_documents([c.content for c in batch])

        points = [
            qm.PointStruct(
                id=_stable_point_id(chunk.chunk_id),
                vector={DENSE_VECTOR_NAME: dense_vec},
                payload=chunk_to_payload(chunk, _domain_from_source_uri(chunk.source_uri)),
            )
            for chunk, dense_vec in zip(batch, dense_vecs)
        ]
        client.upsert(collection_name=collection_name, points=points)
        total += len(points)

    return total


def build_index(
    client: QdrantClient | None = None,
    embedder: EmbeddingBackend | None = None,
    *,
    recreate: bool = True,
    tenant_ids: tuple[str, ...] = ("MGV", "MHB"),
) -> int:
    """Full pipeline: load corpus -> chunk -> embed -> index. Used by
    ``make seed``-style setup and by tests."""
    client = client or get_client()
    embedder = embedder or get_embedding_backend()
    chunks = chunk_all(tenant_ids)

    if recreate and client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)

    return index_chunks(client, chunks, embedder)


if __name__ == "__main__":
    n = build_index()
    print(f"Indexed {n} chunks into Qdrant collection {COLLECTION_NAME!r}.")
