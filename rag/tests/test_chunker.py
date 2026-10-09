"""T037 — chunker tests. No Qdrant required — pure content -> Chunk."""

from __future__ import annotations

import hashlib

from content.chunker import chunk_all, chunk_tenant
from graph.models import Chunk


def test_chunk_all_produces_chunk_instances() -> None:
    chunks = chunk_all()
    assert len(chunks) > 0
    assert all(isinstance(c, Chunk) for c in chunks)


def test_chunk_ids_unique() -> None:
    chunks = chunk_all()
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate chunk_id across corpus"


def test_tenant_id_populated_and_correct() -> None:
    for tenant_id in ("MGV", "MHB"):
        chunks = chunk_tenant(tenant_id)
        assert chunks, f"no chunks produced for {tenant_id}"
        assert all(c.tenant_id == tenant_id for c in chunks)


def test_source_uri_and_content_hash_populated() -> None:
    for chunk in chunk_all():
        assert chunk.source_uri.startswith("content/corpus/")
        assert chunk.content_hash == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        assert chunk.content.strip() != ""


def test_valid_from_populated_for_all_chunks() -> None:
    # Every corpus file declares a top-level valid_from — every chunk
    # should inherit it (directly or via its item-level override).
    for chunk in chunk_all():
        assert chunk.valid_from is not None


def test_entertainment_domain_has_valid_to_window() -> None:
    # entertainment.yaml declares a corpus-level valid_to (event-calendar
    # staleness window) — this is the "stale-content filter" the plan.md
    # CRITICAL section calls out for Chunk.valid_to.
    chunks = [c for c in chunk_all() if "entertainment.yaml" in c.source_uri]
    assert chunks
    assert all(c.valid_to is not None for c in chunks)


def test_no_cross_tenant_ids_leak_into_wrong_tenant() -> None:
    mgv_chunks = chunk_tenant("MGV")
    mhb_chunks = chunk_tenant("MHB")
    mgv_ids = {c.chunk_id for c in mgv_chunks}
    mhb_ids = {c.chunk_id for c in mhb_chunks}
    assert mgv_ids.isdisjoint(mhb_ids)


def test_policy_clauses_chunk_independently() -> None:
    # Structure-aware chunking requirement: policy clauses (e.g. the
    # jurisdiction-specific gaming clause) are separately retrievable chunks,
    # not folded into one giant policies blob.
    chunks = chunk_tenant("MGV")
    clause_ids = {c.chunk_id for c in chunks if c.chunk_id.startswith("mgv-pol-")}
    assert "mgv-pol-gaming-responsible" in clause_ids
    assert "mgv-pol-checkin" in clause_ids
    assert clause_ids != {"mgv-pol-gaming-responsible", "mgv-pol-checkin"} or len(clause_ids) >= 2


def test_faq_chunks_one_per_question() -> None:
    chunks = chunk_tenant("MHB")
    faq_chunks = [c for c in chunks if "faq.yaml" in c.source_uri]
    assert len(faq_chunks) == 10  # mhb/faq.yaml has 10 entries
