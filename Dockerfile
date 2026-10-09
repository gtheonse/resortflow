# resortflow-orchestrator / hold-ledger-reaper -- see docs/adr/050-root-orchestrator-image-and-entrypoint.md.
# Both docker-compose services share this one image: `hold-ledger-reaper` overrides
# `command:` to run `python -m infra.jobs.hold_ledger_reaper` instead of uvicorn.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

# Derived from actual `import` statements across graph/api.py, graph/supervisor/,
# graph/mcp_client.py, graph/ledger.py, agents/*/tools.py, rag/retriever.py,
# rag/embeddings.py, observability/telemetry.py, observability/redaction.py, and
# policy/injection.py -- not a generic list. python-multipart/aiosqlite (sims-only) and
# mcp (gateway-only) are deliberately absent, matching mcp/Dockerfile's own per-service
# scoping.
RUN pip install --no-cache-dir \
    fastapi uvicorn httpx pydantic \
    langgraph langgraph-checkpoint-postgres "psycopg[binary]" \
    mcp pyjwt tenacity \
    opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http \
    opentelemetry-instrumentation-openai-v2 \
    qdrant-client~=1.11.0 \
    sentence-transformers rank-bm25 langchain-text-splitters \
    openai "langfuse>=4.15,<5" \
    presidio-analyzer presidio-anonymizer spacy \
    pyyaml

# ADR-031: presidio-analyzer's NER backend, vendored at build time so no network
# egress is needed at first use in the container (specs/006 T056, previously blocked
# on "no root Dockerfile exists yet").
RUN python -m spacy download en_core_web_sm

# rag/retriever.py's CrossEncoderReranker and rag/embeddings.py's local fallback
# embedder, pre-cached at build time for the same no-network-at-first-use reason.
# Both load lazily in application code (only when Azure OpenAI embeddings aren't
# configured, for the embedder) -- pre-downloading here means a cold container never
# pays a first-request Hugging Face Hub fetch even on that fallback path.
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')" \
 && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY mcp/gateway /app/gateway
COPY observability /app/observability
# feature 009 (FR-013/FR-014c): the prompt templates and lock are baked into the image.
COPY prompts /app/prompts
COPY graph /app/graph
COPY agents /app/agents
COPY rag /app/rag
COPY policy /app/policy
COPY infra /app/infra

EXPOSE 8100
CMD ["uvicorn", "graph.api:app", "--host", "0.0.0.0", "--port", "8100"]
