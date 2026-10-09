"""Track D — hybrid retrieval (dense + BM25 + RRF + cross-encoder rerank).

Exports the public surface Phase 5's supervisor graph will wire in:
``HybridRetriever`` (query -> ``list[graph.models.Chunk]``), plus the
indexing entry point and internal-only models.
"""

from __future__ import annotations

from rag.embeddings import EmbeddingBackend, get_embedding_backend
from rag.indexer import COLLECTION_NAME, build_index, get_client, index_chunks
from rag.menu import get_venue_chunks
from rag.retriever import (
    BM25Retriever,
    CrossEncoderReranker,
    DenseRetriever,
    HybridRetriever,
    is_availability_query,
    payload_to_chunk,
    rrf_fuse,
)

__all__ = [
    "COLLECTION_NAME",
    "BM25Retriever",
    "CrossEncoderReranker",
    "DenseRetriever",
    "EmbeddingBackend",
    "HybridRetriever",
    "build_index",
    "get_client",
    "get_embedding_backend",
    "get_venue_chunks",
    "index_chunks",
    "is_availability_query",
    "payload_to_chunk",
    "rrf_fuse",
]
