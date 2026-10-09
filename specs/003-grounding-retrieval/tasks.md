# Phase 3 Tasks — Grounding & Retrieval

## T036 – Content Corpus Structure & Loaders
**Description:** Create YAML-based content corpus for both tenants (dining, rooms, wellness, entertainment, policies, FAQs).
**Files:** `content/corpus/mgv/*.yaml`, `content/corpus/mhb/*.yaml`, `content/loader.py`
**Acceptance:** All 12 corpus files present; loader.py reads and validates structure

- [x] Create `content/corpus/` directory structure
- [x] Write MGV corpus (dining, rooms, wellness, entertainment, policies, faq)
- [x] Write MHB corpus (dining, rooms, wellness, entertainment, policies, faq)
- [x] Implement `content/loader.py`: load_corpus(tenant_id) → dict of raw content
- [x] Validate: each domain loads without error for both tenants

---

## T037 – Chunking & Metadata Extraction
**Description:** Implement structure-aware chunking producing `graph.models.Chunk` instances
directly — do NOT redefine a parallel Chunk model in `rag/models.py`.
**Files:** `rag/models.py` (new internal-only types), `content/chunker.py`
**Acceptance:** Chunker output is `list[graph.models.Chunk]`; `valid_from`/`valid_to`/
`tenant_id`/`content_hash` all populated; metadata preserved

- [x] Import `Chunk` from `graph.models` — do not redefine
- [x] Define only new internal types in `rag/models.py` (e.g. `RawChunk` pre-embedding,
      `EvalCase`, `RetrievalMetrics`) — nothing that duplicates `Chunk`/`Citation`
- [x] Implement custom chunker in `content/chunker.py` using LangChain RecursiveCharacterTextSplitter
- [x] Populate `Chunk.tenant_id`, `source_uri`, `valid_from`/`valid_to`, `content_hash`
      (hash of chunk content, for change detection / cache invalidation)
- [x] Test chunking on all 12 corpus files; verify `Chunk` instances validate

---

## T038 – Qdrant Setup & Indexing
**Description:** Initialize Qdrant collections, index all chunks with payload filtering.
**Files:** `rag/indexer.py`, `docker-compose.yml` (Qdrant service)
**Acceptance:** Collections exist in Qdrant; all chunks indexed; payload filters working

- [x] Add Qdrant service to docker-compose.yml (already present; added healthcheck)
- [x] Implement `rag/indexer.py`: create collection (dense vector), upsert chunks —
      BM25 deliberately not a Qdrant sparse vector; see rag/retriever.py docstring
- [x] Payload structure: {tenant_id, source_uri, domain, validity_window, chunk_id, content_hash}
- [x] Test: index both tenants; verify collection counts match expected chunk totals

---

## T039 – Hybrid Retrieval (Dense + BM25 + RRF)
**Description:** Implement hybrid search combining dense embeddings, BM25, and RRF fusion.
**Files:** `rag/retriever.py`
**Acceptance:** HybridRetriever returns `list[graph.models.Chunk]` (+ derived `Citation`
objects) ranked by RRF fusion; feeds `graph.state.dedupe_by_id` unmodified in Phase 5

- [x] Implement DenseRetriever: query → embeddings → vector search
- [x] Implement BM25Retriever: query → keyword search
- [x] Implement rrf_fuse: combine dense + BM25 using Reciprocal Rank Fusion
- [x] Payload filter: ALL queries filter to caller's tenant_id
- [x] Test queries for both tenants; verify zero cross-tenant results

---

## T040 – Cross-Encoder Re-ranking
**Description:** Add cross-encoder re-ranking as final quality gate.
**Files:** `rag/retriever.py` (extend), local inference setup
**Acceptance:** Re-ranked results improve NDCG over raw RRF; no degradation on latency

- [x] Integrate cross-encoder/ms-marco-MiniLM-L-6-v2 (local; see plan.md deviation note)
- [x] Implement re-ranker pipeline: RRF results → cross-encoder scores → reorder
- [x] Latency budget: <200ms per query including re-ranking (verified on ~20-candidate pool)
- [x] Test: top-1 relevance improves post-rerank

---

## T041 – Evaluation Set & Baseline Metrics
**Description:** Create 60-question eval set with gold chunks; calculate RAGAS baseline.
**Files:** `rag/eval/eval_set.yaml`, `rag/eval/runner.py`, `rag/eval/results.json`
**Acceptance:** Context recall ≥ 0.85; baseline metrics stored in results.json

- [x] Create 60 diverse questions covering all domains, both tenants
- [x] For each question, manually label gold chunks from corpus
- [x] Implement RAGAS harness: context_recall, context_precision metrics
- [x] Run eval; achieve ≥ 0.85 context recall — measured 0.958
- [x] Store baseline in results.json

---

## T042 – Adversarial Cross-Tenant Tests
**Description:** 200-probe adversarial test to ensure zero cross-tenant leakage.
**Files:** `rag/eval/adversarial_set.yaml`, extend `rag/eval/runner.py`
**Acceptance:** All 200 probes return zero cross-tenant chunks

- [x] Generate 200 adversarial probes (e.g., "book a table at MHB venues" queried as MGV, etc.)
- [x] Run probes; verify payload filters reject cross-tenant matches — measured 0/200 leaks
- [x] Log any violations; fail fast if found (runner.py exits 1 on any leak)

---

## T043 – Negative Tests (Availability Questions)
**Description:** Verify that availability questions (live data, not static) return 0 results.
**Files:** `rag/eval/negative_set.yaml`, extend `rag/eval/runner.py`
**Acceptance:** All 20+ negative questions return empty results

- [x] Create questions like "what tables are available tonight at X?" (not answerable from corpus)
- [x] Run queries; verify zero results — measured 0/20 failures via a deterministic
      `is_availability_query()` short-circuit in the retriever (a relevance-score threshold
      was tried first and let 16/20 through; see the Phase 3 handoff Deviations #2)
- [x] Rationale: availability is live state, not knowledge (belongs in tools, not RAG)

---

## T044 – Integration: RAG Module & Docker Compose
**Description:** Wire RAG module into main stack; Qdrant accessible; health check passes.
**Files:** docker-compose.yml, `rag/__init__.py`, main app setup
**Acceptance:** `docker-compose up` includes Qdrant; retriever instantiable from main app

- [x] Add Qdrant service to docker-compose.yml with persistent volume (already present;
      added healthcheck)
- [x] Implement health check in docker-compose
- [x] Create `rag/__init__.py`: exports Retriever, Indexer, models
- [x] Test: instantiate retriever in main app; execute dummy query
- [x] Verify: `docker-compose up` brings up Qdrant cleanly

---

## Phase 3 Exit Criteria

- [x] Content corpus: 12 YAML files loaded and validated
- [x] Chunking: structure-aware, metadata preserved
- [x] Indexing: all chunks in Qdrant with payload filters
- [x] Retrieval: hybrid (dense + BM25 + RRF) + cross-encoder re-rank
- [x] **Context recall ≥ 0.85** on 60-question gold set
- [x] **Zero cross-tenant leakage** on 200 adversarial probes
- [x] **Negative tests pass:** availability questions return 0
- [x] Docker Compose: Qdrant runs cleanly
