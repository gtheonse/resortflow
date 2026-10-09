# Research: Contracts Foundation

**Feature**: `001-contracts-foundation` | **Date**: 2026-09-09

Phase 0 output. Every Technical Context entry resolved; no `NEEDS CLARIFICATION` remains.

Sources: `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §5–6,
`docs/ResortFlow-HITL-Durability-State.md` §3–5 and §11,
`docs/ResortFlow-Observability-Tracing-Deep-Dive.md` §2–3,
`docs/contracts/openapi/*.yaml`.

---

## 1. Merge-by-key reducers over last-write-wins

**Decision**: Every parallel-written collection in `ResortFlowState` carries an explicit reducer
via `Annotated[list[T], reducer]`. Four reducers: `merge_proposals` (by proposal id),
`merge_holds` (by idempotency key), `append_only` (concatenation), `dedupe_by_id`
(first-seen wins).

**Rationale**: Dining, Entertainment and Wellness execute in the same superstep and all
three write `proposals`. LangGraph's default channel behaviour is last-write-wins, which
silently discards two of the three results. The loss is invisible — no exception, no log
line, just a guest missing two of the three things they asked for. §5 of the plan doc names
this as the single most common bug in hand-rolled multi-agent systems.

**Alternatives considered**:
- *Last-write-wins with a serialised fan-out.* Correct, but forfeits the parallelism that
  justifies the supervisor topology in the first place — §2 of the plan doc cites 9 seconds
  versus 3 as the reason fan-out exists.
- *A single `results` channel written only by a fan-in node.* Pushes merge logic into a node
  where it is untested and unreusable, and loses per-collection semantics: confirmations
  must append, retrieved chunks must dedupe, and holds must respect state precedence. One
  channel cannot express three different merge rules.

---

## 2. Holds merge on `idempotency_key`, not `hold_id`

**Decision**: `merge_holds` keys on `idempotency_key`. `Hold.hold_id` is `str | None`.

**Rationale**: `hold_id` is assigned by the vendor and is therefore null for the entire
window between issuing a request and receiving a response — exactly the window a crash is
most likely to land in. Keying on it would make every in-flight attempt anonymous and
unmergeable. The idempotency key is client-generated from `(session_id, hold_id, attempt)`
per §2 of the tracing doc and exists before the call is made, which is what makes it the
join key across all three crash windows in §3 of the durability doc.

**Alternatives considered**:
- *Key on `(domain, venue_id, time)`.* Fails on the legitimate case of two holds at the same
  venue for the same party at different times, and requires domain-specific knowledge inside
  a generic reducer.
- *Generate a client-side surrogate id separate from the idempotency key.* Two identifiers
  that must be kept in lockstep, with no benefit over using the one that already has to
  exist for the vendor contract.

---

## 3. Terminal-state stickiness in `merge_holds`

**Decision**: `confirmed`, `released`, `expired` and `orphan_released` are terminal and can
never be overwritten by `pending` or `held`. The reducer enforces this precedence rather
than trusting arrival order.

**Rationale**: Resume replays steps rather than continuing from them (§3 of the durability
doc), so a stale write from a replayed node can arrive *after* a newer terminal write.
Without precedence, a replayed `held` would resurrect a hold that the vendor has already
released, and the compensation path would then try to release inventory that no longer
exists. Re-acquiring after expiry legitimately produces a new hold with a new idempotency
key, so it does not need the old row to be mutable.

**Alternatives considered**:
- *Timestamp-based resolution.* Requires trustworthy clocks across the vendor boundary and
  still cannot distinguish a replayed old write from a genuine new one.
- *Version counters per hold.* Correct but heavier, and the state lattice is small enough
  (6 values, 4 terminal) that precedence is simpler to state, test and explain.

---

## 4. `TypedDict` with `Annotated` reducers, not a Pydantic state model

**Decision**: `ResortFlowState` is a `TypedDict`. The domain entities inside it are Pydantic v2
models.

**Rationale**: LangGraph binds reducers through `Annotated` metadata on `TypedDict` fields;
that is the mechanism by which a channel gets merge semantics. A `BaseModel` state has no
per-field channel and therefore no place to attach a reducer, which would collapse the
entire decision in §1. Using Pydantic for the *entities* keeps validation where it matters
without giving up channel semantics.

**Alternatives considered**:
- *Pydantic `BaseModel` for state.* Better ergonomics and runtime validation, but no
  per-channel reducers. Rejected on correctness grounds, not preference.
- *Plain dataclasses for entities.* Serialize acceptably, but lose field validators, which
  are how the structured-dietary and mandatory-idempotency-key constraints are enforced at
  construction rather than at review time.

---

## 5. `JsonPlusSerializer` with no pickle fallback, wrapped in `EncryptedSerializer`

**Decision**: All state content restricted to types `JsonPlusSerializer` handles natively —
Pydantic v2 models, dataclasses, datetimes, enums, primitives. Models set
`extra="forbid"`. Production serde is
`EncryptedSerializer.from_pycryptodome_aes()` reading `LANGGRAPH_AES_KEY`.

**Rationale**: §5 of the durability doc is explicit — reaching for pickle means the state
design is wrong. Beyond design hygiene, pickle payloads are opaque to inspection, which
defeats principle V's auditability requirement, and they are a deserialisation risk.
Separately, checkpoints contain guest names, phone numbers and dietary requirements; the
latter are health data under most privacy regimes, sitting in the database as `BYTEA`.
Opaque is not encrypted. Application-layer encryption on top of transparent database
encryption means a restored backup still does not yield guest health data.

**Alternatives considered**:
- *Rely on database encryption at rest alone.* Protects the disk, not a restored backup or a
  compromised connection. Insufficient for health data.
- *Encrypt selected fields only.* Requires an accurate field inventory that drifts as models
  grow — which is precisely why FR-023 marks sensitivity at field level, but encrypting the
  whole payload is the safer default and costs nothing extra.

---

## 6. Ledger in Postgres, separate from checkpoint tables, `UNIQUE` on `idempotency_key`

**Decision**: A ResortFlow-owned `hold_ledger` table, written outside the checkpointer, with a
uniqueness constraint on `idempotency_key` and indexes on `(status, expires_at)` and
`(saga_id)`.

**Rationale**: This is the feature's central argument. Under `durability="sync"` the
persistence order of `put_writes` and `put` is not enforced — both are submitted to the same
background executor pool — so whether a crash during `put()` leaves a task's writes durable
is decided by the thread scheduler. Combined with replay-on-resume semantics, a node can
re-execute. The conclusion is unavoidable: **the checkpointer is a system of record for
conversation state, not for external side effects.** Those are different durability
requirements and need different stores. The uniqueness constraint is what makes the reaper's
job decidable; `(status, expires_at)` is the index the 60-second orphan sweep runs on.

**Alternatives considered**:
- *Derive external effects from the checkpoint.* The failure this exists to prevent. §11's
  failure matrix shows the `exit`-mode row where the ledger is the *only* recovery path.
- *A separate database for the ledger.* Cleaner isolation, but forfeits transactional
  reasoning and doubles operational surface for a system of this scale. Same database, separate
  schema of record, is the honest middle.
- *Event sourcing the whole saga.* Over-engineered for the volumes involved; the append-only
  `saga_log` in state plus the ledger already provide reconstruction.

---

## 7. `replan_count` with a hard bound of 3, then escalate

**Decision**: `replan_count: int` in state, bounded at 3, with human escalation on breach.
The bound is delivered by this feature as `MAX_REPLAN_COUNT: Final[int] = 3` in
`graph/state.py` — a frozen, importable value rather than a number living only in prose.
The escalation *behaviour* is deferred to feature 005, which builds the planning cycle that
would escalate; recorded in `spec.md` Assumptions rather than left implicit. (Added after
the `/speckit-analyze` pass found the bound had no artifact behind it.)

**Rationale**: Planner → fan-out → conflict-check → replan is a cycle. Cycles in LangGraph
are legal and useful; unbounded cycles are an outage. §5 of the plan doc is direct about
this: every cyclic graph in production needs an explicit termination guarantee, and being
able to state yours is the point. Three attempts is enough for genuine reconvergence and
short enough that a pathological input surfaces to a human quickly.

**Alternatives considered**:
- *Wall-clock timeout.* Non-deterministic, untestable, and interacts badly with the human
  approval interrupt, which legitimately pauses for minutes.
- *Token or cost budget.* Better aligned to spend, but doesn't bound *structural*
  non-convergence — a cheap loop still loops forever.

---

## 8. Telemetry attribute vocabulary frozen now, emission deferred

**Decision**: `observability/attributes.py` holds the frozen names and nothing else. Freezes
the five identifiers from §2 of the tracing doc — `session_id`, `trace_id`, `span_id`,
`saga_id`, `idempotency_key` — the dual `gen_ai.*` / `langfuse.*` namespaces, the `resortflow.*`
namespace, and the cardinality rules.

**Rationale**: §3 of the tracing doc calls the telemetry abstraction the single most
important mitigation in the observability design, and C1 (attribute namespace divergence) is
rated highest risk. The sequencing problem is concrete: Track B emits tool spans in feature
002, five features before observability is built in feature 007. Without a frozen vocabulary,
three tracks invent three vocabularies and feature 007 becomes a renaming pass across saved
dashboards and eval queries. Freezing the *names* costs one constants module now; freezing
the *implementation* would be premature.

Cardinality discipline is part of the contract, not a footnote: `session_id`, `guest_id` and
`saga_id` are span attributes only — unbounded. `tenant_id`, `node`, `domain`, `model` and
`outcome` are safe as metric dimensions — bounded. Putting `guest_id` on a metric is how a
five-figure Log Analytics bill happens.

**Alternatives considered**:
- *Defer entirely to feature 007, per the phase plan.* Follows the schedule literally and
  accepts a rename across five features' worth of instrumentation. The phase plan schedules
  the *work*, not the *contract*.
- *Prose-only freeze in a markdown document.* Unenforceable — no test can catch a stray
  literal. A constants module makes "no attribute names outside this module" checkable.

---

## 9. Field-level PII and health-data marking via `Annotated` metadata

**Decision**: Sensitivity is declared on the field using `Annotated[...]` metadata, so it is
introspectable from the model definition alone.

**Rationale**: §10 of the tracing doc and C7 place redaction on the trace and log write
paths, and the constitution requires it. Both need to know which fields are sensitive. A
separately maintained list drifts the moment a track adds a field — and the drift is silent,
because nothing fails when a new sensitive field is simply absent from the list. Declaring it
on the field means the redaction layer enumerates rather than looks up, and a test can assert
that every field of a known-sensitive entity carries a marker.

**Alternatives considered**:
- *Naming convention (`_pii` suffix).* Zero machinery, but pollutes the wire format and is
  unenforceable against a field named otherwise.
- *A central registry module.* Explicit, but reintroduces the two-places-to-update problem
  the decision exists to remove.

---

## 10. Error classification as an explicit table over two authoritative sources

**Decision**: A pure `classify()` function over an explicit mapping table. Coverage is the
union of the nine failure modes in §11 of the durability doc and the failure-code
enumerations across the five vendor specifications. Unknown code with a 5xx status →
retryable; unknown with 4xx → terminal.

**Rationale**: FR-014 requires totality and determinism, which means the input set must be
finite and enumerable — that is what makes `test_errors.py` able to assert it. An explicit
table is auditable in review; heuristics over message strings are not. The fallback rule
exists so an unrecognised code from a vendor change still resolves deterministically instead
of raising, which would convert a vendor's minor release into a ResortFlow outage.

Policy refusals are structurally separated: they never classify as retryable, because
retrying a compliance block is both futile and, in the responsible-gaming case, exactly the
behaviour the constitution forbids.

**Alternatives considered**:
- *HTTP status codes alone.* Loses the distinction that matters most — `HOLD_EXPIRED` and
  `INSIDE_CANCEL_WINDOW` are both 4xx but demand completely different handling (re-acquire
  versus escalate).
- *Let each MCP server classify its own vendor.* Five inconsistent implementations of the
  logic whose entire purpose is consistency.

---

## 11. Graph node names frozen now, node logic deferred to feature 005

**Decision**: `graph/nodes.py` holds the full node-name registry as string constants,
transcribed from §4's `stateDiagram-v2` and reconciled against the roster table. No node
behaviour is implemented — that is feature 005.

**Rationale**: AGENTS.md's hard rules are explicit that a node name is never renamed in a
patch release, and the constitution's CONTRACT STABILITY standard treats node names the
same way as graph state keys. The reason is structural, not stylistic: LangGraph's Postgres
checkpoint schema keys `checkpoint_ns` and each write's `task_path` by node identity, so a
rename after checkpoints exist in production is a persistence-format break rather than a
refactor — the same class of problem `test_state_contract.py` exists to catch for state
keys. This decision was not in the original scope of this feature; it surfaced during a
review against `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §4, which found that
`telemetry-attributes.md` (item 8, above) already declares `node` a bounded metric
dimension with nothing anywhere defining the bound. Freezing the registry here closes that
inconsistency the same way item 8 closed the equivalent one for attribute names.

One source inconsistency is carried forward rather than resolved: §4's roster table lists
an Offer Agent and a Human Handoff node that the diagram does not draw as distinct states.
Both names are reserved in the registry; whether each becomes its own node is left as an
open question for feature 005, stated explicitly in `data-model.md` rather than decided
here by default.

**Alternatives considered**:
- *Defer entirely to feature 005, per the phase→feature map.* Follows the schedule
  literally, but leaves the telemetry doc's cardinality claim unbounded for five features
  and lets Track C pick names unilaterally when it starts — the identical drift-by-omission
  argument already rejected for the telemetry vocabulary in item 8.
- *Freeze only the five domain-subgraph names, deferring the orchestration nodes.* Would
  satisfy the `Domain` enum cross-reference but not the telemetry inconsistency, since spans
  are emitted by orchestration nodes (`planner`, `conflict_check`, `human_approval`, …) at
  least as often as by domain subgraphs.

---

## 12. Packaging and tooling

**Decision**: `pyproject.toml` at the repository root, packages `graph` and `observability`,
`mypy --strict` scoped to both, `ruff` for lint, `pytest` for tests,
`openapi-spec-validator` to satisfy the specs-lint-clean exit criterion.

**Rationale**: The phase exit criteria in §13 of the plan doc require that the state schema
"imports and type-checks" and that the OpenAPI specs "lint clean". Both are only meaningful
if actually executed, which requires a real environment. Strict mode is scoped to the two
contract packages rather than the whole tree so that later tracks are not blocked by strict
typing on exploratory simulator code.

**Alternatives considered**:
- *A `src/` layout.* Conventional for distributable packages, but invalidates every path in
  the existing track-ownership table for no benefit in a non-distributed application.
- *No type checking, tests only.* Loses the specific exit criterion the phase defines, and
  type errors in a contracts library surface as runtime failures in seven dependent tracks.
