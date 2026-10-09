# ADR-051 — Langfuse-aware OTLP exporter wiring, session_id span attribute, headless provisioning

**Status**: Accepted
**Date**: 2026-09-15
**Feature**: 008-phase-7-5-gap-closure

**Note on numbering**: continuing the register's already-non-sequential real
numbering (`019-write-ahead-hold-ledger.md`'s own "Note on numbering" is the
precedent this follows). This is the next real number after
`049-local-dev-identity-and-edge-no-separate-services.md`; `050` is a
parallel decision for the Dockerfile/orchestrator entrypoint, not addressed
here.

## Context

A read-only audit of the local observability path (Phase 7.5 gap closure)
found `.env`/`.env.example` set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`
— nothing in `docker-compose.yml` listens on port 4318. `observability/
telemetry.py`'s `_build_provider()` (ADR-041) then unconditionally appended
`/v1/traces` to whatever endpoint it was given and set no auth headers. Both
facts together mean the exporter has never successfully delivered a span
anywhere in this build: wrong port, and even at the right port, wrong path
and no credentials.

This is a wiring gap, not a design gap. `specs/007-eval-observability-gate/
research.md` already considered and rejected a dedicated `otel-collector`
service as unnecessary indirection — Langfuse's own native OTLP ingestion
endpoint was always the intended target. This ADR reaffirms that prior
decision rather than relitigating it; it exists because the endpoint that
decision named was never actually wired up correctly in code or config.

`docs/ResortFlow-Observability-Tracing-Deep-Dive.md` §6 is explicit about what
correct wiring looks like: Langfuse's OTLP ingestion path is
`http://<host>:3000/api/public/otel` — not the generic `/v1/traces` suffix
any other OTLP collector would use — authenticated with HTTP Basic auth
(base64 of `<public_key>:<secret_key>`) plus a mandatory
`x-langfuse-ingestion-version: 4` header, without which the same doc warns
directly-ingested data can lag by up to ten minutes.

Separately, the same audit found `start_agent_span()` (the sole span-creation
entry point, ADR-041) never set a session/thread identifier on any span,
despite `observability/attributes.py` already documenting the exact string
(`langfuse.session.id`) for exactly this purpose, and `ui/README.md`
disclosing the gap explicitly: "session_id/trace_id still aren't attached as
span attributes anywhere." Without it, Langfuse cannot group a guest's
spans across nodes into one session view — a `ResortFlowState.session_id` already
exists on every state dict (`graph/state.py`) and was simply never read.

Finally, Langfuse needs a project and a matching API keypair to exist before
it will accept ingestion — with no UI-driven setup step in this project's
local-dev flow, that has to happen headlessly at container start.

## Options Considered

**1. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at a dedicated `otel-collector`
service instead of fixing the direct Langfuse path.** Already rejected by
`specs/007-eval-observability-gate/research.md` — an extra service with its
own container, config, and failure mode, for a translation layer Langfuse's
own native ingestion endpoint makes unnecessary. Revisiting it here would
relitigate a settled decision rather than fix the actual bug (a wrong path
and missing headers on the endpoint that was always the right target).

**2. Hardcode the Langfuse-specific path and headers unconditionally in
`_build_provider()`.** Simplest change. Rejected: it would silently break
the production path, where `OTEL_EXPORTER_OTLP_ENDPOINT` points at Azure
Monitor / Foundry and Basic-auth headers and a Langfuse-specific version
header have no meaning — a direct violation of ADR-041's cloud-agnostic
commitment (no vendor-specific behavior baked into the one shared telemetry
module without a config gate).

**3. (Chosen) Branch inside `_build_provider()` on whether
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are both set.** When both are
present, target `{endpoint}/api/public/otel` with Basic auth and the
ingestion-version header. When either is absent, keep the existing generic
`{endpoint}/v1/traces` path with no extra headers — the exact shape any
other OTLP-speaking backend expects, Azure Monitor included. The module
still imports no vendor SDK and still reads its exporter target from the
standard `OTEL_EXPORTER_OTLP_ENDPOINT` variable; the branch is on presence
of two already-existing, already-optional env vars, not a new
Langfuse-specific configuration surface.

## Decision

**Option 3**, plus two adjacent fixes bundled into the same pass because
they were found by the same audit and touch the same file:

1. **Exporter wiring** (`observability/telemetry.py::_build_provider`): when
   `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are both set, build the
   `OTLPSpanExporter` against `{endpoint}/api/public/otel` with an
   `Authorization: Basic <base64(public:secret)>` header and
   `x-langfuse-ingestion-version: 4`. Otherwise, unchanged generic
   `/v1/traces` behavior. The existing `except Exception: pass` guard is
   kept (exporter misconfiguration must never break the graph) but now logs
   a warning with `exc_info=True` first — silently swallowing a real config
   bug was harder to debug than the fix is worth carrying forward.

2. **`session_id` span attribute** (`start_agent_span`): a new
   keyword-only, optional `session_id: str | None = None` parameter, set as
   `langfuse.session.id` when present. `wrap_node`'s sync and async
   wrappers, and `agents/dining/tracing.py`'s `traced_node` (which already
   has `state` in scope), now pass `state.get("session_id")` through.
   `mcp/gateway/_shared/tracing.py` was checked and left unchanged: its
   `initialize_tracer`/`get_tracer` are called once per gateway process,
   not per request, with no state dict or session id in scope to thread
   through — forcing a value here would mean fabricating one, which is
   worse than the disclosed gap it would replace.

3. **Headless Langfuse provisioning**: `.env`'s `OTEL_EXPORTER_OTLP_ENDPOINT`
   moves to `http://localhost:3000` (this repo's convention: `.env` holds
   host-process values, `docker-compose.yml`'s `environment:` blocks hold
   container-DNS values). `.env` gets a generated local dev keypair
   (`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`); `.env.example` keeps them
   blank as a placeholder, consistent with every other secret in that file.
   The same key values are reported for `docker-compose.yml`'s `langfuse`
   service so Langfuse provisions itself, at container start, with a
   project whose keys match what the exporter is already authenticating
   with — no manual UI setup step. That compose edit is out of scope for
   this ADR's author (another process integrates all compose changes in one
   pass); the exact block is:

   ```yaml
   LANGFUSE_INIT_ORG_ID: resortflow-local
   LANGFUSE_INIT_ORG_NAME: ResortFlow Local Dev
   LANGFUSE_INIT_PROJECT_ID: resortflow-local
   LANGFUSE_INIT_PROJECT_NAME: ResortFlow
   LANGFUSE_INIT_PROJECT_PUBLIC_KEY: pk-lf-local-dev-example
   LANGFUSE_INIT_PROJECT_SECRET_KEY: sk-lf-local-dev-example
   LANGFUSE_INIT_USER_EMAIL: dev@resortflow.local
   LANGFUSE_INIT_USER_NAME: ResortFlow Dev
   LANGFUSE_INIT_USER_PASSWORD: resortflow-dev-local-only
   ```

## Consequences

**Made easier.** Local traces actually land in Langfuse for the first time
in this build's history — the exporter now points at a listening port, the
correct ingestion path, with valid auth. A guest's whole session is
groupable in the Langfuse UI via `langfuse.session.id`, closing a gap
`ui/README.md` had disclosed but not fixed. `make up` plus a fresh
`docker-compose.yml` (once the reported env block is merged) needs no manual
Langfuse project setup.

**Made harder.** Two env vars (`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`)
now silently change exporter behavior for anyone touching
`observability/telemetry.py` — a future reader has to know their presence
is the branch condition, not just decoration. Documented here and in the
module's own docstring comment at the branch site.

**Foreclosed.** Nothing structural. The generic `/v1/traces` path stays the
default for any backend that isn't Langfuse, so ADR-041's cloud-agnostic
requirement is unaffected — this ADR narrows behavior only inside an
already-optional, already Langfuse-specific env-var pair.

## Addendum — corrections found during live verification (2026-09-16)

The above was written and unit-tested before a real end-to-end run against the live
docker-compose stack was possible. That live run (Phase 7.5's final verification pass)
found three more, real gaps this ADR's original text did not anticipate — each confirmed
by an actual failure, then fixed and re-verified against a live trace landing in
Langfuse's UI, not assumed correct from documentation:

1. **The exporter endpoint was still missing `/v1/traces`.** `OTLPSpanExporter`'s
   `endpoint` parameter is used verbatim — it does not auto-append `/v1/traces` the way
   the SDK's environment-variable-driven default does. `{endpoint}/api/public/otel` (no
   suffix) produced a silent `404 Not Found` on every export, visible only by checking
   the orchestrator's own logs (`Failed to export span batch code: 404`), not by the
   exporter raising. Fixed: `{endpoint}/api/public/otel/v1/traces`.
2. **Langfuse v3's OTel/event ingestion is not optional-S3, it's required-S3.** With no
   working S3-compatible endpoint configured, every ingested batch failed
   server-side (`Failed to upload JSON to S3`, then bare `Internal Server Error` once a
   syntactically-valid-but-unreachable placeholder endpoint was tried instead of no
   endpoint at all). `docker-compose.yml` gained a `minio`/`minio-init` pair (a bucket
   named `langfuse`, credentials `langfuse-minio`/`langfuse-minio-local-only`) — the
   minimal real S3-compatible service this requires locally, mirroring `azurite`'s
   existing role for Azure Blob. `minio/minio:latest` on Docker Hub returned "pull
   access denied" (Docker Hub distribution changes since this stack's images were
   chosen) — `quay.io/minio/minio` and `quay.io/minio/mc` are the working images.
3. **Langfuse v3 ships web and worker as two separate images, not one.** The `langfuse`
   container (`langfuse/langfuse:3`) only runs `web/server.js` — confirmed by inspecting
   the image directly (`docker inspect`'s `Config.Cmd`, and no `worker` directory
   anywhere under `/app`). It publishes every ingested OTel batch to a Redis-backed
   queue and returns; nothing consumes that queue without a second container. Every
   export from `observability/telemetry.py` hung for the full retry window
   (`Read timed out`) waiting on a response that depended on work nothing was doing.
   `docker-compose.yml` gained a `langfuse-worker` service (`langfuse/langfuse-worker:3`,
   same environment as `langfuse` via a YAML anchor) to actually process the queue.

None of these three were guessable from `docs/ResortFlow-Observability-Tracing-Deep-Dive.md`
alone — the doc's own §6 stops at "point the exporter here with these headers," which is
necessary but was not sufficient. This addendum exists specifically so a future reader
does not have to rediscover the same three failures by hand: after all four fixes
(exporter path, `session_id`, MinIO, worker), a real turn through `graph/api.py`
produces a trace retrievable from `GET /api/public/traces`, grouped by
`langfuse.session.id`, with `gen_ai.system`/`gen_ai.operation.name` present — verified
live, not asserted.
