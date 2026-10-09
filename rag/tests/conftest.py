"""Shared fixtures for Track D (content/rag) tests.

These are integration tests against a real local Qdrant (``docker compose
up -d qdrant``) — there is no in-memory Qdrant fake in this codebase, and
faking hybrid search + payload filtering would test the fake, not the
tenant-isolation guarantee this phase exists to prove. The whole module is
skipped (not failed) when Qdrant isn't reachable, so the rest of the suite
still runs in an environment without Docker.
"""

from __future__ import annotations

import pytest

from rag.embeddings import get_embedding_backend
from rag.indexer import build_index, get_client
from rag.retriever import HybridRetriever


def _qdrant_reachable() -> bool:
    try:
        client = get_client()
        client.get_collections()
        return True
    except Exception:  # noqa: BLE001 — any connection error means Qdrant is unavailable
        return False


requires_qdrant = pytest.mark.skipif(
    not _qdrant_reachable(), reason="Qdrant not reachable at QDRANT_URL — run `docker compose up -d qdrant`"
)


@pytest.fixture(scope="session")
def embedder():
    # force_local=True: tests must run without real Azure credentials.
    return get_embedding_backend(force_local=True)


@pytest.fixture(scope="session")
def indexed_client(embedder):
    """Builds (or rebuilds) the full corpus index once per test session."""
    client = get_client()
    build_index(client=client, embedder=embedder, recreate=True)
    return client


@pytest.fixture(scope="session")
def retriever(indexed_client, embedder):
    return HybridRetriever(client=indexed_client, embedder=embedder, use_reranker=True)


@pytest.fixture(scope="session")
def retriever_no_rerank(indexed_client, embedder):
    return HybridRetriever(client=indexed_client, embedder=embedder, use_reranker=False)
