"""T039 — Dense embedding backend, with a local fallback.

Prefers Azure OpenAI (``text-embedding-3-small``, matching plan.md's Tech
Stack) when ``AZURE_OPENAI_ENDPOINT`` / ``AZURE_OPENAI_API_KEY`` are
configured. Falls back to a local ``sentence-transformers`` model
(``all-MiniLM-L6-v2``) when they are not, so the indexer, retriever, and
eval harness all run offline in this environment without real Azure
credentials — per the task brief's "gracefully handle missing API keys by
using a local embedding model as fallback so tests can run without real
credentials."

Both backends implement the same tiny protocol (``embed_documents``,
``embed_query``, ``dimension``) so ``rag/indexer.py`` and
``rag/retriever.py`` don't need to know which one is active.
"""

from __future__ import annotations

import os
from typing import Protocol


class EmbeddingBackend(Protocol):
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class AzureOpenAIEmbeddingBackend:
    """Dense embeddings via Azure OpenAI. Requires
    ``AZURE_OPENAI_ENDPOINT``/``AZURE_OPENAI_API_KEY`` (see .env.example)."""

    def __init__(self, deployment: str | None = None) -> None:
        from openai import AzureOpenAI

        self._client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
        # Feature 008 fix: this previously always used the "text-embedding-3-small"
        # default literally, regardless of what's actually deployed under that name
        # in the target Azure/Foundry resource -- found live via 404 DeploymentNotFound
        # once load_dotenv() (ui/app.py) made real credentials reachable for the first
        # time. Mirrors eval/judge.py's pinned_model_id()'s
        # FOUNDRY_*_DEPLOYMENT_NAME-then-plain-Azure-default pattern.
        self._deployment = deployment or os.environ.get(
            "FOUNDRY_EMBEDDING_DEPLOYMENT_NAME", "text-embedding-3-small"
        )
        # Feature 008 fix: `dimension` used to be a hardcoded class attribute
        # (1536, text-embedding-3-small's size) -- wrong for any other deployed
        # model (found live: the real configured deployment returns 3072-dim
        # vectors, text-embedding-3-large's size). rag/indexer.py's
        # ensure_collection reads this to size the Qdrant collection, so a
        # wrong hardcoded value would have made every real insert fail against
        # a wrongly-sized collection. Derived from one real embed call instead
        # of assumed, mirroring LocalSentenceTransformerEmbeddingBackend's own
        # dynamic (not hardcoded) dimension detection below.
        self.dimension = len(self.embed_query("dimension probe"))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(input=texts, model=self._deployment)
        return [d.embedding for d in resp.data]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class LocalSentenceTransformerEmbeddingBackend:
    """Offline fallback: a small local ``sentence-transformers`` model.
    No network calls once the model is cached locally by huggingface_hub."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        from rag import _env_compat  # noqa: F401 — local-environment shim, see module docstring

        self._model = SentenceTransformer(model_name)
        try:
            self.dimension = self._model.get_embedding_dimension()
        except AttributeError:  # older sentence-transformers
            self.dimension = self._model.get_sentence_embedding_dimension()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def _azure_configured() -> bool:
    return bool(os.environ.get("AZURE_OPENAI_ENDPOINT")) and bool(
        os.environ.get("AZURE_OPENAI_API_KEY")
    )


_backend_singleton: EmbeddingBackend | None = None


def get_embedding_backend(force_local: bool = False) -> EmbeddingBackend:
    """Return a process-wide singleton embedding backend: Azure OpenAI when
    configured, otherwise the local sentence-transformers fallback.
    ``force_local=True`` (used by tests) always returns the local backend
    regardless of environment configuration."""
    global _backend_singleton
    if _backend_singleton is not None and not force_local:
        return _backend_singleton

    if not force_local and _azure_configured():
        backend: EmbeddingBackend = AzureOpenAIEmbeddingBackend()
    else:
        backend = LocalSentenceTransformerEmbeddingBackend()

    if not force_local:
        _backend_singleton = backend
    return backend
