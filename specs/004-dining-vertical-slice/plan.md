# Phase 4 — Vertical Slice: Dining
## One Domain, End-to-End, Correct — Before Multi-Agent Complexity

**Phase Goal:** Complete dining booking flow from cold start through confirmation, with checkpointing and resumability.

**Exit Criteria:**
- Booking completes from cold start
- Kill process mid-flow and resume correctly (Postgres checkpointer)
- Allergen mismatch is blocked (structured extraction + fidelity check)
- 25-case eval set passes (context, flow, edge cases)

## CRITICAL: Build on frozen contracts from 001-contracts-foundation — do not duplicate

`graph/state.py`, `graph/models.py`, `graph/nodes.py`, `graph/errors.py` are **already built
and frozen** (feature 001, Phase 0, commits `47cc803`..`e5e86f7`). This is not scaffolding to
recreate — it is the persistence contract every domain subgraph, including dining, must write
into. Re-deriving parallel models (a custom `DiningState`, a custom `DietaryInput`) is exactly
the kind of drift AGENTS.md's "contract changes go through a single integration commit" rule
exists to prevent.

**What already exists and MUST be reused, not redefined:**
- `graph.state.ResortFlowState` — the canonical TypedDict. The dining subgraph reads/writes this,
  not a bespoke `DiningState`. Relevant channels: `proposals` (reducer: `merge_proposals`),
  `holds` (reducer: `merge_holds`), `confirmations` (`append_only`), `failures`
  (`append_only`), `saga_log` (`append_only`), `awaiting_approval`, `approval_scope`.
- `graph.models.Domain.DINING` — the domain enum value used on every `Proposal`/`Hold` this
  subgraph emits.
- `graph.models.DietaryRequirement` / `DietaryRestriction` (14 values) / `DietarySeverity`
  (4 levels: `preference`, `intolerance`, `allergy_moderate`, `allergy_severe`) — the
  structured dietary model. `allergy_severe` must reach the kitchen as an alert; `detail`
  (free text) is supplementary and never the sole carrier of a restriction.
- `graph.models.Proposal` — what venue discovery + availability produce (has
  `hold_idempotency_key`, `dietary: list[DietaryRequirement]`, `party_size`, `starts_at`).
- `graph.models.Hold` — keyed by `idempotency_key` (never `hold_id`, which is vendor-assigned
  and null until the vendor responds). `status: HoldStatus`, terminal states never regress
  (`merge_holds` enforces this already).
- `graph.models.Confirmation` — append-only once a hold is promoted.
- `graph.models.SagaEvent` — append-only audit trail (`hold_placed`, `commit_attempted`, etc).
- `graph.errors.ErrorClass` + `classify()` — the four-way disposition
  (`RETRYABLE`/`TERMINAL`/`REQUIRES_HUMAN`/`POLICY_BLOCKED`). Dining's 7 vendor error codes
  (`SLOT_UNAVAILABLE`, `INVALID_PARTY_SIZE`, `VENUE_CLOSED`, `HOLD_EXPIRED`,
  `INSIDE_CANCEL_WINDOW`, `RATE_LIMITED`/`RATE_LIMIT_EXCEEDED`,
  `UPSTREAM_ERROR`/`UPSTREAM_TIMEOUT`) are already mapped — see
  `specs/001-contracts-foundation/data-model.md` lines 353-359.
- `graph.nodes.DINING` (`"dining"`) — the **frozen node name** for this entire subgraph as
  seen from the supervisor (feature 005, not built yet). The subgraph's *internal* nodes
  (intent parse, venue discovery, availability, dietary capture, allergen check, hold,
  confirm) are an implementation detail beneath that single external node — free to name
  sensibly, but keep them stable once chosen since Phase 5 will invoke this subgraph as-is.

**What Phase 4 legitimately adds (does not exist yet):**
- The dining subgraph's internal node implementations (nothing behind `DINING` exists yet)
- Intent parsing (LLM) — no model owns this yet
- Allergen fidelity check logic (cross-referencing `DietaryRequirement` against venue menu
  content) — new logic, though it *consumes* the existing `DietaryRequirement` model
- The MCP tool call plumbing from graph nodes to the Phase 2 dining gateway
- The 25-case eval set

---

## Architecture Overview

### Dining Subgraph (LangGraph)
```
START
  ↓
Intent Parse (user request → structured intent)
  ↓
Venue Discovery (MCP: list_venues, optionally filtered by cuisine/diet/time)
  ↓
Availability Check (MCP: check_availability for chosen venue + party_size + date/time)
  ↓
Dietary Capture (structured form: restrictions, allergies, occasion)
  ↓
Allergen Fidelity Check (venue menu allergies vs. guest restrictions → conflict?)
  ↓
Hold Placement (MCP: hold_table with guest_id, party_size, time, dietary notes)
  ↓
Approval Interrupt (human approval required for dietary conflicts or high-cost comps)
  ↓
Confirm Hold (MCP: confirm_reservation with hold_id)
  ↓
Booking Confirmation (return reservation details, cancellation token)
  ↓
END

[Checkpointer: Postgres — every node state saved]
[Resumability: kill at any point, restart from last checkpoint]
```

### State Structure
Uses `graph.state.ResortFlowState` directly — no bespoke `DiningState`. Channels this subgraph
touches: `tenant_id`, `guest_id` (read-only, set at entry), `messages` (intent parsing),
`proposals` (`merge_proposals`), `holds` (`merge_holds`), `confirmations` (`append_only`),
`failures` (`append_only`), `awaiting_approval`, `approval_scope`, `saga_log`
(`append_only`). Scratch data that doesn't belong in the shared 20-key contract (raw
`IntentParsed`, candidate venue lists before a `Proposal` forms) lives in a local
`TypedDict` extension inside `agents/dining/`, never as a new `ResortFlowState` key — adding a
key to `ResortFlowState` is a breaking change requiring an ADR (AGENTS.md hard rule).

### Allergen Fidelity Check (Policy-Critical)
1. Guest dietary input already arrives as `list[DietaryRequirement]` (structured
   `restriction` + `severity` enums, `detail` free text only supplementary) — never
   re-derive this from free text inside the dining subgraph itself; that parsing is the
   intent/dietary-capture node's job, output must conform to the existing model.
2. Query venue menu from corpus (Phase 3 RAG — `Chunk`/`Citation` models, already defined
   in `graph.models`)
3. Scan menu items for allergen mentions matching any `DietaryRestriction` in the guest's
   list
4. If conflict found and `severity == DietarySeverity.ALLERGY_SEVERE` (or
   `ALLERGY_MODERATE`):
   - Set `awaiting_approval = True`, `approval_scope = "dietary_conflict:{proposal_id}"`
   - Record a `SagaEvent` (`kind="dietary_conflict_flagged"`)
   - Escalate for HITL approval OR suggest an alternative venue
   - `preference`/`intolerance` severities do not block — informational only

---

## Tech Stack
- **Orchestration:** LangGraph StateGraph (synchronous, for Phase 4 simplicity), operating
  on `ResortFlowState`
- **LLM:** Azure OpenAI gpt-4.1-mini (per AGENTS.md tech stack — not "gpt-4-mini")
- **Tools:** Existing Phase 2 MCP dining tools (list_venues, check_availability, hold_table,
  confirm_reservation, release_hold, cancel_reservation, join_waitlist)
- **Persistence:** Postgres LangGraph checkpointer (`langgraph-checkpoint-postgres`,
  already a project dependency), `EncryptedSerializer` per AGENTS.md tech stack
- **Tracing:** Langfuse (every node logged) — `langfuse` service already in docker-compose
- **Eval Framework:** 25 test cases (context variations, edge cases, failures)

---

## File Structure

```
resortflow/
├── agents/
│   └── dining/
│       ├── __init__.py
│       ├── graph.py               # StateGraph over ResortFlowState; compiles to node graph.nodes.DINING
│       ├── state.py               # Local TypedDict extension for subgraph-scratch fields only
│       ├── nodes/
│       │   ├── __init__.py
│       │   ├── intent_parser.py   # user message -> scratch IntentParsed + DietaryRequirement list
│       │   ├── venue_discovery.py # MCP list_venues -> Proposal[] into state["proposals"]
│       │   ├── availability.py    # MCP check_availability -> refine Proposal
│       │   ├── allergen_check.py  # DietaryRequirement vs RAG venue menu -> conflict?
│       │   ├── hold_placement.py  # MCP hold_table -> Hold into state["holds"]
│       │   └── confirm.py         # MCP confirm_reservation -> Confirmation into state["confirmations"]
│       ├── edges/
│       │   ├── __init__.py
│       │   └── conditions.py      # conflict -> interrupt(); no availability -> waitlist
│       └── eval/
│           ├── test_cases.yaml
│           ├── runner.py
│           └── results.json
└── requirements additions to pyproject.toml: langfuse, azure-openai (or openai) client
```

No `graph/dining_entrypoint.py` — the compiled dining subgraph is exposed as a callable/
`CompiledGraph` from `agents/dining/graph.py`; feature 005's supervisor imports it directly
and mounts it under the existing `graph.nodes.DINING` name when the supervisor graph is
built. Phase 4 also ships a thin standalone runner (e.g. `agents/dining/graph.py:invoke()`
or a `__main__` block) so the vertical slice is exercisable before a supervisor exists.

---

## Key Design Decisions

### Why Synchronous LangGraph in Phase 4?
Async fan-out (multiple domains in parallel) arrives in Phase 5. Phase 4 is focused on:
- Single-domain correctness
- Checkpointing + resumability (the hard part)
- Allergen logic (policy-critical)

Synchronous simplifies testing and debugging. Async arrives when we can measure the benefit (3-domain parallelism).

### Why Build on `ResortFlowState` Instead of a Bespoke State?
Feature 001 already froze the shared state contract specifically so that "every domain
subgraph reads and writes this state" (graph/state.py docstring). Inventing a parallel
`DiningState` here would mean a costly reconciliation in Phase 5 when the supervisor tries
to mount this subgraph — the entire point of building the contract first was to avoid that.

### Why Allergen Fidelity in Phase 4?
Allergen handling is policy-critical (liability) and cross-cutting (affects all booking flows). Validating it in a single, isolated domain first prevents bugs that would propagate to all domains later. Plus, dining has the most complex allergen surface (allergies → kitchen → dish composition).

### Why Structured Dietary Input (already enforced)?
`graph.models.DietaryRequirement` already forces this: `restriction` (14-value enum) and
`severity` (4-value enum) are both required fields; `detail` (free text) can accompany but
never substitute for them. This subgraph's intent/dietary-capture node must *produce*
`DietaryRequirement` instances — it does not get to invent a looser shape.

### Why Postgres Checkpointer from the Start?
Phase 4 must prove resumability works. Using Postgres checkpointer (the production choice) ensures the integration is real, not a future tech debt. SQLite alternative is tempting but would require rework in Phase 8.

---

## Success Criteria Checklist

- [ ] LangGraph StateGraph defined over `ResortFlowState`, with all internal nodes
- [ ] Intent parser: user request → scratch `IntentParsed` + `list[DietaryRequirement]`
- [ ] Venue discovery: query list_venues, filter by constraints, emit `Proposal` objects
- [ ] Availability check: call MCP tool, refine `Proposal`
- [ ] Allergen fidelity: `DietaryRequirement` vs RAG venue menu (Phase 3 `Chunk`/`Citation`)
- [ ] Hold placement: call MCP hold_table, emit `Hold` (keyed by `idempotency_key`)
- [ ] Approval interrupt: HITL on conflict via `awaiting_approval`/`approval_scope`
- [ ] Confirm: call MCP confirm_reservation, emit `Confirmation`
- [ ] Postgres checkpointer: checkpoint created after each node
- [ ] Resumability: kill mid-graph, restart → continue from checkpoint
- [ ] Langfuse tracing: every node logged with full context
- [ ] Vendor errors classified via `graph.errors.classify()` into `ToolFailure`
- [ ] **All 25 test cases pass**
- [ ] Allergen mismatch scenario: blocks correctly (severe/moderate → interrupt)
