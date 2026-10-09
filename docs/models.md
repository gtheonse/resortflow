# Model assignment — build-time

**Scope: this file governs which Claude model is used in Claude Code while executing a
spec-kit task.** It says nothing about which model the product calls at inference time.

> **Runtime tiering lives in `docs/model-tiering-runtime.md`** — which Azure OpenAI model
> each graph node calls when serving a guest. Keep the two vocabularies separate: they share a principle and nothing else,
> and conflating them means debugging a production incident while wondering which tier
> table someone meant.

|  | Build-time (this file) | Runtime (`model-tiering-runtime.md`) |
|---|---|---|
| Who chooses | You, in Claude Code | The graph, per node |
| Models | Opus 5 / Sonnet 5 / Haiku 4.5 | `gpt-4.1` / `gpt-4.1-mini` |
| Cost borne by | Your subscription | Per guest conversation |
| Selection | Per task, at authoring time | Per node, at design time |
| Dynamic? | No — declared in `tasks.md` | No — declared per node |

Rationale for both being static is in `docs/ResortFlow-Model-Tiering.md` §3.

---

## Tier map

| Tier | Model | Claude Code command |
|------|-------|---------------------|
| `FRONTIER` | Claude Opus 5 | `/model opus` |
| `MID` | Claude Sonnet 5 | `/model sonnet` |
| `ECONOMY` | Claude Haiku 4.5 | `/model haiku` |

Adjust to whatever you have access to and want to spend. Collapsing all three to one model
still works — the prompts are unchanged, you just pay more or get weaker code depending
which way you collapse it.

---

## The rule

> **ECONOMY only where the spec fully determines the output. If executing the task requires
> a judgment call that isn't written down, it is not ECONOMY.**

Everything below is elaboration on that one sentence.

It has a corollary worth taking seriously: **if a task can't be executed by the cheap model,
the spec wasn't specific enough.** Tier assignment doubles as a spec review — every task you
are forced to bump to MID because "the model needs to work out X" is a spec defect found
before implementation rather than during it. That is usually worth more than the cost saving.

---

## `[CRIT]` — never economy tier

Derived from the constitution's non-negotiables. A task carrying `[CRIT]` never runs on
ECONOMY, regardless of how mechanical it looks.

| Area | Why it's critical | Phase |
|---|---|---|
| **State reducers** (`merge_proposals`, `merge_holds`) | Fails *silently*. Last-write-wins looks like working code and drops two of three parallel results. Nothing catches it until Phase 5. | 0 |
| **Error taxonomy + classification logic** | Mis-classifying Terminal as Retryable produces infinite retry against a vendor. | 0 |
| **Hold ledger recovery paths** | Three crash windows, each with a different recovery action. Money on the table. | 0, 5 |
| **Idempotency key derivation** | A weak key means duplicate bookings under retry. | 0, 2 |
| **Tenant filter construction** | Cross-tenant leakage is a reportable incident. | 2, 3 |
| **Saga commit ordering + compensation** | Ordering is the whole design; getting it wrong strands guests mid-booking. | 5 |
| **Conflict checker** | Deterministic arithmetic — but wrong arithmetic silently books impossible itineraries. | 5 |
| **Policy engine, responsible gaming, self-exclusion** | Regulatory. Structural enforcement is the point. | 6 |
| **Allergen fidelity check** | Safety-critical. | 4, 6 |
| **Eval metric implementation + judge prompts** | A broken evaluator makes every other metric decorative. | 7 |
| **All ADRs** | Decisions are long-lived and cited by later work; wording errors propagate. | All |

What these share: **being wrong is silent.** That is the actual bar — not a label.

---

## MID

Real judgment needed, but errors surface fast.

- Orchestration wiring
- MCP tool adapters
- Subgraph assembly
- Retrieval pipeline plumbing
- CI workflow
- Non-trivial test design

## ECONOMY

The spec fully determines the output.

- Sim implementations generated from the frozen OpenAPI specs
- Pydantic models from a written contract
- Seed and synthetic data
- `docker-compose.yml`, `Makefile`
- Docstrings, README prose
- Mechanical refactors
- Test cases derived from an explicit criteria table

---

## Honest expectation

ResortFlow skews **higher** than a typical 60–70% economy split. Its core is concurrency and
distributed-transaction logic — precisely the category where cheap models produce
plausible-looking wrong answers.

The sims (Phase 1) are the one genuinely large economy block: five services generated from
frozen specs. Tier aggressively there and in Phase 3.

**Phases 0, 5, 6 and 7 run entirely on FRONTIER.** Don't tier them. The saving isn't worth
the risk on concurrency, saga, policy and eval code. In Phase 0 specifically, reducers and
the hold-ledger recovery paths are the two things in this project that fail silently, and
both live there.

The point of tiering is avoiding frontier spend on boilerplate — not squeezing every task
downward.

---

## The failure mode to watch

Not "the cheap model produced code that doesn't work." It's **"the cheap model produced code
that works and is wrong."** An ECONOMY model reads less context and honours fewer
constraints, so under-specified ECONOMY tasks yield plausible code that quietly violates the
constitution.

The related risk is **tier drift under pressure**: when a phase runs long, everything gets
bumped downward to move faster. The `[CRIT]` table plus the analyze gate below is the guard —
but only if you actually run analyze.

---

## The analyze gate

Tier assignment is intent until something checks it. Paste this into `/speckit-analyze`:

```
Flag any task marked ECONOMY whose acceptance criteria require a judgment
not fully specified in plan.md. Flag any [CRIT] task not on FRONTIER.
```

---

*Build-time tiering per `docs/ResortFlow-Model-Tiering.md` §1–§2. Runtime tiering per §3 and
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §10.*
