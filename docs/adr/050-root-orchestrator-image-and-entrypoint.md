# ADR-050 — Root orchestrator image and production entrypoint

**Status**: Accepted
**Date**: 2026-09-15
**Feature**: 008.5-phase-7-5-gap-closure

**Note on numbering**: this ADR was drafted in an isolated worktree that branched off
before ADR-049 (`049-local-dev-identity-and-edge-no-separate-services.md`, also part of
this same feature) had been committed, so it was initially self-numbered 048 against a
snapshot where 047 was the highest ADR on disk. It is filed here as **ADR-050**, the
correct next number now that ADR-049 exists. The `tenant_id`/no-edge-auth risk this
document references below is the one ADR-049 formally covers — see that ADR for the
full decision; it is not a hypothetical here.

## Context

`docker-compose.yml` has declared two services that reference a root build context
since at least feature 005: `resortflow-orchestrator` (`build: {context: .}`, port 8100) and
`hold-ledger-reaper` (same build context, `command:` overridden to
`python -m infra.jobs.hold_ledger_reaper`). No root `Dockerfile` has ever existed to
satisfy that context. This was tracked explicitly and left open deliberately, not by
oversight: `specs/006-policy-guardrails-hitl/tasks.md` T056 ("BLOCKED — Vendor
`en_core_web_sm` into a Docker image build per ADR-031 — blocked on a genuine Track F
prerequisite: no root `Dockerfile` exists yet... and no orchestrator FastAPI
app/entrypoint module exists anywhere in the repo to point a `CMD` at either"). It was
outside that phase's track ownership to close.

The second half of that blocker — no orchestrator entrypoint module — was also
genuinely true until this feature. Every prior consumer of `graph.supervisor
.compile_supervisor_graph` was either a CLI (`graph/demo.py`) or the feature-008
verification console (`ui/app.py`), which its own `ui/README.md` documents as dev-only:
SSE streaming, a dev-only per-thread fault-injection toolbar (`X-Sim-Fault` headers),
and a `load_dotenv()` call specifically because it is launched outside docker-compose.
None of that is appropriate to run as the production-shaped service the compose file
already names `resortflow-orchestrator`.

## Options Considered

**1. Promote `ui/app.py` itself to the production entrypoint, gating the dev-only
pieces behind an environment flag.** Rejected: it would leave one module serving two
audiences with materially different trust and operational assumptions (SSE for a human
watching a console vs. a plain synchronous API for a caller service; a fault-injection
surface with real production side effects if the gating flag were ever misconfigured).
Constitution-adjacent hard rules in this repo consistently favor a structural
separation over a runtime toggle for exactly encapsulate this kind of risk.

**2. A separate Dockerfile per orchestrator concern (one for the API, one for the
reaper).** The reaper (`infra/jobs/hold_ledger_reaper.py`) has no dependency the API
image lacks — it needs the same Postgres client and the same `graph`/`infra` code on
its `PYTHONPATH`. Building a second image would mean maintaining two dependency lists
in lock-step for zero isolation benefit; docker-compose's own `command:` override
mechanism (already used by `hold-ledger-reaper` today) is the right-sized tool for "same
image, different process."

**3. One root `Dockerfile`, one new production entrypoint (`graph/api.py`), reusing
`ui/runtime.py`'s checkpointer/ledger wiring, and `hold-ledger-reaper` shares the same
image via compose's existing `command:` override.** Chosen.

## Decision

**One root `Dockerfile`; `graph/api.py` as the production FastAPI entrypoint;
`hold-ledger-reaper` shares that same image.**

The checkpointer/`HoldLedger`-construction logic that used to live entirely inside
`ui/runtime.py`'s `GraphRuntime`/`build_runtime()` (Postgres when `POSTGRES_URL` is set,
else `MemorySaver`/`NullHoldLedger`) moved to a new `graph/runtime.py` — a production
concern `graph/api.py` needs too, not a console-only one. `ui/runtime.py` now wraps
`graph.runtime.build_runtime()` and layers its own fault-aware `GraphRuntime` subclass
on top, so the console's dev-only per-thread `X-Sim-Fault` toolbar
(`_thread_aware_tools_factory`, `_NextCallFaultRegistry`) keeps working unchanged and
never ships inside the production image (the root `Dockerfile` does not `COPY ui/` at
all — deliberately, since `ui/` pulls in `python-dotenv` and a fault-injection surface
that has no business existing in a deployed image).

`graph/api.py` replicates `ui/app.py`'s turn/approve/clarify/reconnect logic against
the same `compile_supervisor_graph` entrypoint, including the two already-debugged
resume-payload bugs that module's own comments document (a `human_approval` resume
needs both `action` and `approved` keys together, since the cross-domain gate and each
domain's own approval node read different keys; a `clarify` resume is a bare string, a
different shape entirely) — copied deliberately, not re-derived, since re-deriving them
risks reintroducing bugs that were only found by running the real graph live. It drops
SSE (JSON-only, one response per call — streaming is a console-only concern) and
`load_dotenv()` (container env comes from compose's `environment:` block, since this
module — unlike `ui/app.py` — IS launched via compose).

`graph/turn_summary.py` is a new module holding the tool-call/hold/guardrail/interrupt
shaping `ui/inspector.py` used to own outright. It excludes the two enrichments that
need `eval/` (LLM-judge groundedness scoring, a cost placeholder) — those stay
console-only, layered back on by `ui/inspector.py`, which now wraps
`graph.turn_summary.summarize_turn`. Splitting it this way keeps `eval/` (Langfuse
judge providers and all) out of the production image entirely, and avoids running a
synchronous LLM-judge call on every production turn response — a real latency/cost
concern a production entrypoint has no business introducing on its own.

The image itself follows the existing `mcp/Dockerfile`/`sims/*/Dockerfile` house style:
`python:3.11-slim`, `PYTHONPATH=/app`, hand-listed `pip install` (no
requirements.txt/pyproject install), `COPY mcp/gateway /app/gateway` (every domain's
`agents/*/tools.py` imports `gateway.*` as a top-level package, the same trick
`mcp/Dockerfile` already uses for the gateway process itself), plus `observability`,
`graph`, `agents`, `rag`, `policy`, `infra`. It adds `RUN python -m spacy download
en_core_web_sm` (closing T056's spaCy half, ADR-031) and pre-caches both
`sentence-transformers` models `rag/retriever.py`'s `CrossEncoderReranker` and
`rag/embeddings.py`'s local fallback embedder load, so a cold container makes no
first-request Hugging Face Hub fetch on either path.

## Consequences

**Made easier.** `specs/006-policy-guardrails-hitl/tasks.md` T056 is closeable —
the exact prerequisite it named ("no root Dockerfile exists yet, and no orchestrator
FastAPI app/entrypoint module exists anywhere") is now satisfied by this feature, not
worked around. `resortflow-orchestrator` and `hold-ledger-reaper` in `docker-compose.yml` now
resolve to a real image instead of an empty build context. A caller integrating against
ResortFlow gets a single JSON-only contract (`POST /v1/turns`, `GET /v1/threads/{id}`,
`POST /v1/threads/{id}/approve`, `POST /v1/threads/{id}/clarify`) with no SSE framing
or console-only fields to filter out.

**Made harder.** Two entrypoints now exist against the same `compile_supervisor_graph`
core (`ui/app.py` and `graph/api.py`), and two `summarize_turn`-shaped functions
(`graph.turn_summary.summarize_turn` and `ui.inspector.summarize_turn`, the latter
wrapping the former) — a future change to the resume-payload contract or the tool-call
shaping has two call sites to keep in sync, not one, if it isn't made in the shared
`graph/` module both actually import from. Kept as one shared module rather than two
independent copies specifically to bound that risk.

**Foreclosed.** No mutating tool call, resume-payload shape, or node name changed to
make this entrypoint work — `graph/api.py` is composition against
`compile_supervisor_graph`'s existing public surface, same as `ui/app.py` and
`graph/demo.py` before it. AGENTS.md's "never rename a graph node in a patch release"
rule was never at risk here.

**Deliberately not fixed here.** `tenant_id` arrives in `graph/api.py`'s request body
with no edge authentication in front of the service — identical to `ui/app.py`'s
existing, already-shipped exposure. Real end-user auth (Entra ID / Azure APIM) is
Phase 8 scope. As noted above, no ADR-049 exists yet to formally charter that
deferral — this ADR is the record of the decision until one
does.
