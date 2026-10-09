# ResortFlow Verification Console (feature 008)

A local, dev-only tool to manually verify the supervisor graph is working —
type a guest message, watch it run, inspect what happened. **Not** part of
the eval suite or the CI gate. `agents/`, `mcp/`, and `eval/` are untouched
by this feature. `graph/supervisor/` has two small, disclosed exceptions
(not part of the `ui/` package itself) — see "Two graph/supervisor/ fixes"
below.

## Run it

```
make up && make seed
python -m rag.indexer          # one-time: populate Qdrant (see "RAG setup" below)
python -m ui.run                # NOT `python -m uvicorn ui.app:app` on Windows -- see why below
```

Then open `http://localhost:8420`.

Without `make up` running, the server still starts and the static shell,
fault catalog, and receipt endpoints work (see `ui/tests/`), but a real turn
will hang/fail trying to reach the MCP gateway.

### Why `python -m ui.run`, not the plain uvicorn CLI, on Windows

`ui/app.py` calls `load_dotenv()` at import time (no other module in this
repo does — everything else assumes docker-compose's `environment:`/
`env_file:` already populated `os.environ`, which isn't true for this
console, a plain local process). Once real credentials are reachable,
`ui/runtime.py`'s Postgres checkpointer path becomes live for the first
time, and psycopg's async mode needs a selector-based event loop —
Windows' asyncio default (`ProactorEventLoop`) isn't compatible, and
`asyncio.set_event_loop_policy(...)` at import time does **not** fix this
under `python -m uvicorn`'s own CLI bootstrap (tested; still crashed).
`ui/run.py` runs uvicorn programmatically with
`asyncio.run(..., loop_factory=...)` instead, which does work. See that
module's own docstring for the full chain.

### RAG setup

`python -m rag.indexer` needs `.env`'s Azure/Foundry embedding credentials
reachable, same reason as above — run it as
`python -c "from dotenv import load_dotenv; load_dotenv(); from rag.indexer import build_index; build_index()"`
if you're not sourcing `.env` into your shell some other way first. If your
Azure resource is on a low tier (S0), a small quota can rate-limit even one
indexing batch — request an increase at the link the 429 error itself
prints if this happens.

## What is implemented vs. deferred

**Must-haves, built and unit-tested:**
- Chat transcript, SSE-streamed (`POST /api/turns`, fetch+ReadableStream on
  the client — native `EventSource` can't POST, so this uses the standard
  fetch-streaming workaround instead)
- Node execution order per turn, parallel fan-out shown as parallel
  (`stream_mode="updates"` — a step with >1 key is one superstep, i.e.
  genuinely concurrent nodes, not sequential ones rendered together)
- Tool calls (from `saga_log` + `failures` — there is no finer-grained
  per-tool-call log in `ResortFlowState` today; building one would mean touching
  `agents/`, out of scope here)
- **Fault-injection toolbar**: real `X-Sim-Fault` values only
  (`timeout, error_500, rate_limit, hold_expired, inventory_race,
  webhook_delay` — verified equal to `sims._shared.faults.ALL_FAULT_VALUES`
  in `ui/tests/test_runtime_and_receipts.py`, not just assumed). Next-call-
  only semantics are built here (`ui/runtime.py`'s `_NextCallFaultRegistry`)
  since nothing server-side provides that — a fresh per-thread tool client
  is constructed with the header for exactly one call, mirroring
  `graph/demo.py`'s own process-scoped trick.
- **Hold TTL countdown** on both the hold cards and the approval card,
  client-side, from `Hold.expires_at`
- Guardrail verdicts (`PolicyFlag` list) and escalation display
- Interrupt/approval flow: `POST .../approve` returns 202 + a receipt token
  immediately; the client polls `GET /api/receipts/{token}` and only ever
  renders "Confirmed" once that poll returns `succeeded` — never on the 202
  itself
- **All-or-nothing approval, per ADR-047** — not simulated as granular. The
  client still sends `approved_domains` as a full-set/empty list (per
  instruction), even though the backend routing only reads `action` today;
  see ADR-047 for why per-item approval isn't safe to fake in a UI.
- Session = LangGraph `thread_id`, shown + copyable in the header; entering
  an existing `thread_id` calls `GET /api/threads/{id}/state` to rehydrate
  the transcript and re-show a pending approval card if one exists

**Deferred (explicitly, per the half-day timebox):**
- **Cost/token panel** — `observability/telemetry.py` now emits real
  `gen_ai.usage.*`/`gen_ai.request.model` per LLM call (separate commit,
  verified), but this console doesn't yet query those spans by trace_id and
  price them. `ui/inspector.py`'s `_groundedness`/cost fields report an
  honest `"not_instrumented"` status rather than a fabricated number.
- **Langfuse trace link** — `session_id`/`trace_id` still aren't attached
  as span attributes anywhere (a pre-existing gap, found during feature 008
  scoping, not fixed here), so there's no ID to build a trace URL from yet.

**Groundedness** is a direct import of `eval.retrieval_scorer
.score_faithfulness` (per instruction — not a subprocess, not a service
call), run live against the turn's actual retrieved context. It degrades to
an honest `"unavailable"` status on `JudgeUnavailableError` exactly like the
eval gate's own `INCOMPLETE` verdict when no judge credentials are reachable.
**Verified working end-to-end** once `.env` is actually loaded (see below):
`{"status": "scored", "score": 1.0, "judge_model": "...", "judge_provider":
"azure-foundry"}` on a real turn.

## Fixes found and made while testing this console against the live stack

Not part of the `ui/` package itself, but recorded here since this README
is where a future reader would look for "why does X behave this way." All
explicitly approved before being made; all outside the directories this
feature was originally scoped to touch.

1. **`graph/supervisor/` — single-domain bookings never got a
   confirmation message.** `DETERMINISTIC` (the ~60%-of-traffic fast path)
   routes straight to `END`, bypassing `RESPOND` (the node that composes
   "All set — confirmed: dining."); `confirm.py`'s success path never
   appended one either. Fixed by extracting `RESPOND`'s logic into
   `_compose_respond_message` and calling it from `make_deterministic_node`
   whenever the inner subgraph didn't already produce its own message.
2. **`graph/supervisor/` — `knowledge_qa`'s retrieval failure silently
   swallowed every exception with zero visibility.** A real
   `DeploymentNotFound`/`RateLimitError` from the embedding backend looked
   identical from the outside to a genuine "nothing retrieved" case — both
   fell through to `CLARIFY` with no trace anywhere. Now
   `logger.warning(..., exc_info=True)`, mirroring `agents/dining/menu.py`'s
   existing pattern for the same kind of graceful-degradation path.
3. **`rag/embeddings.py` — `AzureOpenAIEmbeddingBackend` hardcoded both its
   deployment name (`"text-embedding-3-small"`, ignoring
   `FOUNDRY_EMBEDDING_DEPLOYMENT_NAME`) and its `dimension` (`1536`, wrong
   for any deployment that isn't literally that model — the real configured
   deployment returns 3072-dim vectors). Both real bugs, not configuration
   — every real (credentialed) indexing/retrieval call was broken before
   this. Deployment name now read from env (mirroring `eval/judge.py`'s
   `FOUNDRY_*_DEPLOYMENT_NAME`-then-default pattern); dimension now derived
   from one real probe embed call instead of assumed.

None of this was reachable before, because **no code in this repo calls
`load_dotenv()`** — every real-credential code path (Postgres checkpointer,
Azure embeddings, the eval judge) had silently been running against
whichever fallback exists for "no credentials," indistinguishable from
"credentials configured but broken," until `ui/app.py` added the first
`load_dotenv()` call in the codebase.

## Known limitations (not fixed here — out of scope)

- `mypy ui --ignore-missing-imports --follow-imports=silent` is clean.
  Deliberately **not** added to `pyproject.toml`'s `[tool.mypy]` `files`
  list: doing so would pull `graph/supervisor/`, `mcp/gateway/*`, and
  `rag/*` into the checked set (all reachable via `ui/app.py`'s imports),
  surfacing 59 pre-existing, unrelated errors in those files that a bare
  `mypy` run already has today (confirmed by running it before this
  package existed) — same "don't inherit blame for someone else's gap"
  posture the project already takes with `agents/dining` being deliberately
  excluded from the strict list.
