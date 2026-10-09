# AGENTS.md

Guidance for AI coding agents and human contributors working in this repository.
`CLAUDE.md` imports this file; keep the two consistent by editing only this one.

## Project

**ResortFlow — a multi-agent resort concierge platform.** An enterprise multi-agent platform that
lets a guest of a two-property luxury resort group plan and book across rooms, dining,
entertainment, wellness, and (read-only) gaming in one conversation.

The core problem is a **distributed transaction behind a natural-language front door**: the
backing systems share no transaction coordinator, so every mutation is a reversible hold
followed by a confirmed commit, with compensation when part of a plan fails.

All client data is anonymized and fictional. The client is **Meridian Resorts International
(MRI)**; never introduce real client, property, or brand names into this repository.

| Tenant | Property | Jurisdiction / regulator |
|---|---|---|
| `MGV` | Meridian Grand, Las Vegas | Nevada — Nevada Gaming Control Board (NGCB) |
| `MHB` | Meridian Harbor, Boston | Massachusetts — Massachusetts Gaming Commission (MGC) |

The two jurisdictions are load-bearing: they force per-tenant *policy*, not just per-tenant
branding.

## Architectural commitments

Everything follows from these five. Do not violate one without an ADR.

1. **Ambiguity-based routing.** LLMs are used only where intent is unbounded. About 60% of
   traffic never enters the agentic graph. Deterministic paths stay first-class and are the
   degradation fallback.
2. **All tool access through MCP.** No agent holds a vendor SDK. One governed integration
   surface, typed schemas, per-tool scopes.
3. **Every external mutation is a two-phase hold-then-confirm.** No agent performs an
   irreversible action in a single call.
4. **Policy is not a prompt.** Compliance rules (allergen handling, responsible gaming,
   jurisdiction) are enforced in code, before and after the model.
5. **State is durable and inspectable.** Every graph step is checkpointed. Any conversation
   is replayable, resumable, and auditable.

## Hard rules

- **Never** put an LLM where arithmetic will do. The conflict checker is deterministic on
  purpose.
- **Never** let an agent place a wager or execute a gaming transaction. The gaming domain is
  read-only.
- **Never** carry a dietary restriction only in free text. Use the structured enum plus
  severity, asserted before commit.
- **Never** trust `state["holds"]` on resume. Revalidate against the vendor.
- **Never** derive `tenant_id` from model output. It comes from the token claim, injected
  server-side.
- **Never** rename a graph node in a patch release. Node names are a persistence contract
  (checkpoints store them).
- **Never** read, print, or commit `.env` or any credential. Only `.env.example` is tracked.

## Tech stack

- **Orchestration:** LangGraph (Python), a supervisor plus domain subgraphs
- **Checkpointer:** Postgres (`langgraph-checkpoint-postgres`) with `EncryptedSerializer`
- **Vector search:** Qdrant — hybrid dense + BM25, reciprocal rank fusion, cross-encoder rerank
- **LLM:** Azure OpenAI — gpt-4.1-mini default, gpt-4.1 for planner/recovery/judge
  (an Azure AI Foundry project endpoint is also supported)
- **Tools:** MCP servers, one per domain, behind one gateway
- **Simulators:** FastAPI, stateful, fault-injectable (five simulated vendor APIs)
- **Observability:** Langfuse locally; OpenTelemetry to Azure Monitor and Foundry in production
- **Local environment:** docker-compose; everything runs offline except the LLM calls

## Repository layout and module ownership

Modules are file-disjoint. Stay inside the directories that belong to the change you are
making; cross-cutting changes go through a single integration commit.

| Track | Directories | Purpose |
|---|---|---|
| A — Simulators | `sims/`, `docs/contracts/openapi/` | Five stateful vendor simulators and their frozen OpenAPI contracts |
| B — MCP layer | `mcp/` | MCP gateway and one tool server per domain |
| C — Agents | `agents/`, `graph/`, `ui/` | Domain subgraphs, supervisor, orchestrator API, dev console |
| D — Retrieval | `content/`, `rag/` | Tenant corpora, indexing, hybrid retrieval |
| E — Eval | `eval/` | Evaluation suite and merge gate |
| F — Infra | `infra/`, `docker/` | Jobs, SQL init, container images |
| G — Policy | `policy/` | Code-enforced guardrails |
| Cross-cutting | `observability/`, `prompts/` | Telemetry attributes and redaction; the prompt registry |

Other top-level paths: `docs/` (architecture, ADRs, contracts), `specs/` (feature specs),
`tests/` (cross-cutting tests), `.specify/` and `.claude/skills/` (spec-driven tooling).

`prompts/` is the prompt registry: agents and eval code call `prompts.registry.resolve()`, and
`langfuse` is imported only in `prompts/langfuse_adapter.py`. `observability/` is deliberately
not folded into `graph/`: several tracks import attribute names from it, so it must not live
inside one track's directory. `ui/` is a dev-only verification console and is never copied into
the production `Dockerfile`.

## Commands

```bash
make up              # full local stack: infra + simulators + gateway + orchestrator
make seed            # generate a dev signing key if missing, seed 2 tenants, venues, events, rooms, guests
make demo            # scripted multi-domain booking with an injected dining failure
make eval            # per-domain evaluation suites (the CI gate runs `python -m eval.runner`)
make eval-adversarial  # gaming adversarial suite; slow, so not part of `make eval`
make test-docker     # full pytest suite in a Linux container
make mcp             # simulators + MCP gateway only
```

Without Docker:

```bash
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
pytest -m "not integration"        # unit tests; `integration` needs credentials or live services
ruff check .
mypy                               # strict; scope is set in pyproject.toml
python -m prompts.sync validate    # lint prompt templates offline
python -m eval.runner --tier pr    # the PR-tier evaluation gate
```

The compose `tests` service deliberately does not load `.env`, so unit tests never see live
credentials. For a credentialed run, pass them explicitly with `--env-from-file .env`.

## Conventions

- **Python 3.11+**, ruff line length 100, `mypy --strict` on the packages listed in
  `pyproject.toml`. Async by default for I/O.
- **Contracts are frozen.** The five OpenAPI vendor contracts in `docs/contracts/openapi/`,
  the tool-error schema, and the MCP tool contract do not change unilaterally. If a change is
  needed, raise it as an issue first. Contract drift is the main risk in parallel work.
- **Decisions are recorded as ADRs** in `docs/adr/NNN-slug.md` with Status, Context, Options
  Considered, Decision, and Consequences. A change that constrains later work, reverses a
  commitment above, or trades one project value against another needs an ADR.
- **Tests before the next feature.** Saga failure permutations and tenant isolation are
  mandatory coverage. Property-based tests (Hypothesis) cover `policy/`.
- **Every model call declares its tier** statically per node; see
  `docs/model-tiering-runtime.md`. No dynamic model routing.
- **Prompts are versioned templates** under `prompts/templates/`, resolved through the
  registry, and covered by the eval gate. Do not inline prompt text in agent code.
- **Never log guest PII or health data** outside `observability/redaction.py`'s boundary.

## Spec-driven workflow

Features are built through the Spec Kit workflow, driven by the skills in `.claude/skills/`:

`specify → clarify → plan → tasks → analyze → implement → converge`

Specs live in `specs/NNN-feature-name/` (spec, plan, tasks, research, data model, quickstart).
The project constitution is `.specify/memory/constitution.md`. If a workflow step is skipped,
record the reason in an ADR rather than proceeding silently. Do not dispatch implementation
outside the `implement` step.

## Documentation map

Documentation is large; read only what the task needs.

| Document | Read when |
|---|---|
| `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` | Start here: business case, agent roster, state design, phase plan |
| `docs/ResortFlow-Supervisor-Architecture.md` | Changing the supervisor, saga participants, or graph topology |
| `docs/ResortFlow-HITL-Durability-State.md` | Changing checkpointing, the hold ledger, human approval, or resume |
| `docs/ResortFlow-Observability-Tracing-Deep-Dive.md` | Changing telemetry, tracing, or evaluation reporting |
| `docs/ResortFlow-Model-Tiering.md`, `docs/model-tiering-runtime.md` | Adding or changing a model-calling node |
| `docs/ResortFlow-Cloud-Portability-Review.md` | Reasoning about Azure coupling and cross-cloud mapping |
| `docs/contracts/` | Touching a simulator, the MCP gateway, or the tool-error envelope |
| `docs/adr/` | Understanding why something is the way it is |
