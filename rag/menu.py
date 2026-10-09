"""Exhaustive per-venue chunk lookup — the real implementation behind
`agents/dining/menu.py`'s `query_venue_menu` interface.

This is deliberately NOT `HybridRetriever.retrieve`. The allergen check is
policy-critical (AGENTS.md: dietary restrictions never carried only in free
text, asserted before commit) and must see every menu-item chunk for a
venue, not the top-k semantically ranked by a query string — a relevance
cutoff could drop the one chunk that names the guest's allergen, which is
exactly the silent failure the "never put an LLM/score where arithmetic
will do" principle exists to prevent (see rag/retriever.py's
`is_availability_query` docstring for the same reasoning applied
elsewhere). So this scrolls Qdrant with a deterministic tenant_id +
domain="dining" filter and narrows to one venue client-side by
`source_uri`, rather than going through the ranked retrieval path.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from graph.models import Chunk
from rag.indexer import COLLECTION_NAME, get_client
from rag.retriever import payload_to_chunk


def get_venue_chunks(
    tenant_id: str,
    venue_id: str,
    *,
    client: QdrantClient | None = None,
    collection_name: str = COLLECTION_NAME,
) -> list[Chunk]:
    """All indexed chunks for one venue's dining domain (overview + every
    menu section), tenant-filtered. Empty list if the collection doesn't
    exist yet or the venue has no indexed content — callers must treat
    that as "nothing to check," never as an error."""
    if not tenant_id:
        raise ValueError(
            "tenant_id is required — never derive it from model output "
            "or venue_id (AGENTS.md hard rule)"
        )

    client = client or get_client()
    if not client.collection_exists(collection_name):
        return []

    tenant_and_domain_filter = qm.Filter(
        must=[
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id)),
            qm.FieldCondition(key="domain", match=qm.MatchValue(value="dining")),
        ]
    )

    venue_marker = f"#{venue_id}."
    chunks: list[Chunk] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=tenant_and_domain_filter,
            limit=256,
            with_payload=True,
            offset=offset,
        )
        for point in batch:
            payload = point.payload
            if venue_marker in payload["source_uri"]:
                chunks.append(payload_to_chunk(payload))
        if offset is None:
            break

    return chunks
