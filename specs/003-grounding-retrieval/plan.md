# Phase 3 — Grounding & Retrieval
## Content Corpus, Chunking, Qdrant Collections, Hybrid Retrieval

**Phase Goal:** Correct, tenant-isolated, freshness-aware knowledge.

**Exit Criteria:**
- Context recall ≥ 0.85 on eval set (60 questions with gold chunks)
- Zero cross-tenant leakage across 200 adversarial probes
- Negative tests pass: availability questions NOT answerable from index

## CRITICAL: Reuse frozen models from 001-contracts-foundation

`graph.models.Chunk` and `graph.models.Citation` are **already defined and frozen**
(feature 001, commit `47cc803`). Do not redefine them in `rag/models.py`.

- `graph.models.Chunk`: `chunk_id`, `tenant_id` ("isolation is enforced at query time,
  never by the model" — the docstring itself states the payload-filter architecture this
  phase must implement), `content`, `source_uri`, `valid_from`/`valid_to` (the "stale-content
  filter" — i.e. the validity-window metadata this phase's chunking must populate),
  `content_hash`.
- `graph.models.Citation`: `chunk_id`, `source_uri`, `quoted_span`.
- `graph.state.dedupe_by_id` — the reducer that merges retrieved chunks into
  `ResortFlowState["retrieved"]`, already keyed on `chunk_id`, first-occurrence-wins (preserves
  RRF rank order). The retriever's output must be `list[Chunk]` so this reducer applies
  unmodified when Phase 5 wires retrieval into the supervisor graph.

`rag/models.py` should therefore contain only what's new: internal chunking/indexing types
that never cross into `ResortFlowState` (e.g. a pre-embedding `RawChunk`, `EvalCase`,
`RetrievalMetrics`) — not a parallel `Chunk`.

---

## Architecture Overview

### Retrieval Pipeline
```
Content Corpus
    ↓
Structure-Aware Chunking
    ↓ (with metadata: validity windows, tenant_id, source)
Embedding + Indexing (Qdrant)
    ↓
Hybrid Search: Dense + BM25 + RRF
    ↓
Cross-Encoder Re-ranking
    ↓
Context Returned (ranked by relevance + tenant filter)
```

### Content Structure
- **Tenant-scoped:**
  - MGV (Las Vegas): venue descriptions, menus, policies, house rules
  - MHB (Boston): venue descriptions, menus, policies, house rules
- **Domain coverage:**
  - Dining: restaurant info, menus, dress codes, cuisine descriptions
  - Rooms: room types, amenities, views, upgrade paths
  - Wellness: spa treatments, fitness offerings, procedures
  - Entertainment: venue capacities, seating, event calendar
  - Gaming: responsible gaming resources, policies (read-only, NOT live odds)
  - General: property info, directions, hours, FAQs

### Tenant Isolation
- Qdrant payload filters ensure tenant_id always matches caller's identity
- No cross-tenant chunk in response under any query
- Adversarial test: attempt to ask "what's at MHB?" from MGV identity → zero results

---

## Tech Stack
- **Vector DB:** Qdrant (hybrid mode)
  - Dense embeddings: Azure OpenAI text-embedding-3-small
  - BM25: Qdrant native
  - RRF (Reciprocal Rank Fusion): fusion of dense + BM25
  - Cross-encoder reranker: BAAI/bge-reranker-base via local inference
- **Content Management:** Headless CMS (simulated: YAML files in `content/corpus/`)
- **Chunking:** LangChain `RecursiveCharacterTextSplitter` with custom metadata extraction
- **Eval Framework:** RAGAS-style context recall/precision baseline

---

## File Structure

```
resortflow/
├── content/
│   ├── corpus/
│   │   ├── mgv/
│   │   │   ├── dining.yaml         # MGV restaurant catalog
│   │   │   ├── rooms.yaml          # MGV room types
│   │   │   ├── wellness.yaml       # MGV spa/fitness
│   │   │   ├── entertainment.yaml  # MGV venues
│   │   │   ├── policies.yaml       # MGV house rules + gaming policy
│   │   │   └── faq.yaml            # MGV FAQ
│   │   └── mhb/
│   │       ├── dining.yaml
│   │       ├── rooms.yaml
│   │       ├── wellness.yaml
│   │       ├── entertainment.yaml
│   │       ├── policies.yaml
│   │       └── faq.yaml
│   └── loader.py                    # Corpus loader + chunking logic
├── rag/
│   ├── __init__.py
│   ├── indexer.py                  # Qdrant indexing, upsert logic
│   ├── retriever.py                # Hybrid search (dense + BM25 + RRF)
│   ├── models.py                   # Content, Chunk, RetrievalResult pydantic models
│   └── eval/
│       ├── eval_set.yaml           # 60 questions + gold chunks
│       ├── adversarial_set.yaml    # 200 cross-tenant probes
│       ├── negative_set.yaml       # Availability questions (should return 0)
│       ├── runner.py               # RAGAS harness
│       └── results.json            # Baseline metrics
├── docker-compose.yml              # Qdrant service
└── requirements.txt                # qdrant-client, langchain, etc.
```

---

## Key Decisions

### Why Structure-Aware Chunking?
Different content types need different chunk boundaries:
- Menus: by-cuisine section (keeps related dishes together)
- Policies: by-clause (regulatory parsing)
- FAQs: by-question (natural boundary)
- Room types: by-room-category (keeps upgrade paths coherent)

**Implementation:** Custom metadata extractors per content type, then overlap-aware splitting.

### Why Hybrid (Dense + BM25)?
- Dense embeddings excel at semantic similarity ("romantic restaurants")
- BM25 excels at keyword matching ("late checkout policy")
- RRF (Reciprocal Rank Fusion) combines both without training a fusion model
- Cross-encoder re-ranking as the final quality gate

### Why Qdrant Payload Filtering?
- Tenant_id in every payload
- Query *always* filters to caller's tenant before returning results
- Structural guarantee: no possibility of cross-tenant leak (not just filtering at retrieval time)

---

## Success Criteria Checklist

- [ ] Content corpus loaded for both tenants (YAML files)
- [ ] Chunker produces valid splits with metadata (tenant_id, source, validity_window)
- [ ] Qdrant indexing: all chunks inserted with payload filters
- [ ] Hybrid retriever: dense + BM25 + RRF ranking working
- [ ] Cross-encoder reranker: top-k quality improvement measured
- [ ] Eval harness: RAGAS metrics calculated
- [ ] **Context recall ≥ 0.85** on 60-question gold set
- [ ] **Zero cross-tenant leakage** on 200 adversarial probes
- [ ] **Negative test pass:** availability questions return 0 results
- [ ] Docker compose up; Qdrant accessible at localhost:6333
