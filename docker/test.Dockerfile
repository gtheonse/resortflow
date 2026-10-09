# resortflow-tests -- Track F. Runs the full pytest suite on Linux, where Windows Smart App Control's
# blocking of unsigned native wheels (spaCy's stateclass*.pyd, psycopg_binary's libcrypto*.dll,
# jsonschema_rs.pyd — see docs/adr/ for the write-up) does not apply. Not a runtime image: this
# installs the `dev` extra and pytest's own dependencies, and copies no source (the compose service
# bind-mounts the repo instead, so no rebuild is needed per code change).
ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim

WORKDIR /app
ENV PYTHONPATH=/app:/app/mcp PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Only pyproject.toml is needed to resolve dependencies -- installing from it directly (rather than
# a second, hand-maintained requirements list) keeps one source of truth with the venv the host uses.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    ".[dev]" \
    "opentelemetry-util-genai==1.1b0" && \
    python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); print(d)" >/dev/null
# opentelemetry-instrumentation-openai-v2 2.4b0 depends on opentelemetry-util-genai with no upper
# bound; an unconstrained install here resolved util-genai 1.2b0, which no longer ships
# `opentelemetry.util.genai.instruments`, so `OpenAIInstrumentor` fails to import. Because
# observability/telemetry._instrument_openai swallows that exception by design, the only symptom
# is "0 auto-instrumented LLM spans" in tests/test_telemetry_openai_instrumentation.py. Pinned to
# 1.1b0, the version in the maintained host venv where the pair works. Test-image-only pin: the
# same unbounded dependency is a latent risk for any fresh production build.

# ADR-031 (mirrors the root Dockerfile): vendored at build time so tests need no network egress.
RUN python -m spacy download en_core_web_sm

# rag/retriever.py's CrossEncoderReranker and rag/embeddings.py's local fallback embedder,
# pre-cached for the same reason (mirrors the root Dockerfile).
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')" \
 && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

CMD ["pytest", "graph", "agents", "eval", "prompts", "observability", "ui", "tests", "-q"]
