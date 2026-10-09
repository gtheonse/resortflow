# Phase 0 Research: Policy, Guardrails & HITL

Five decisions below meet the significance bar and are promoted to ADRs (028-032,
`docs/adr/`). A "Smaller decisions" section follows for routine choices that apply an
already-established project pattern to a new surface.

Two discoveries reframed this feature's actual scope before any decision was made, so
they're recorded first:

**Discovery A — the self-exclusion data source already exists and is already real.**
`mcp/gateway/gaming/server.py`'s `check_rg_status` tool is wired to a live sim endpoint
(`GET /casino/v1/responsible-gaming/status` on `sims/casino`), returning a fully-modeled
`ResponsibleGamingStatus` (`self_excluded`, `exclusion_scope`, `limit_flags`,
`offers_permitted`, `gaming_content_permitted`, `mandated_message`,
`support_resources` — `mcp/gateway/gaming/models.py`). This is not a Phase 6 build item.
What Phase 6 actually closes is a concrete, existing gap: `agents/gaming/nodes/lookup.py`
only calls `check_rg_status` when the guest's own intent is classified `rg_status` — a
guest asking about floor games or loyalty points today gets an answer with **no
self-exclusion check at all**, contradicting the constitution's "before *any*
gaming-domain tool executes... on every turn, regardless of topic."

**Discovery B — `PolicyFlag` and the redaction promise are already reserved contract
fields, unused since Phase 0.** `graph/models.py`'s `PolicyFlag` (`flag_id`, `rule`,
`disposition` ∈ `{blocked, redacted, escalated, messaged}`, `jurisdiction`, `raised_at`)
and `ResortFlowState.policy_flags: list[PolicyFlag]` have existed since the frozen state
contract (feature 001) — every domain's graph initializes the field to `[]` and no node
anywhere writes to it. Likewise `ToolFailure.detail`'s docstring already reads "Redacted
before persistence" while no redaction code exists. Phase 6 is the feature these fields
were reserved for; it activates them rather than inventing parallel state.

Both discoveries mean this feature's real work is concentrated in **closing gaps in
already-scaffolded machinery**, not building a policy subsystem from nothing — which is
exactly why the ADRs below are about wiring and routing decisions, not about picking a
policy-engine framework.

## Decision: Policy Engine is a plain deterministic Python module, not a rules-engine or sidecar

- **Decision**: `policy/` is implemented as ordinary, unit-testable Python functions
  organized by the seven-layer enforcement table in
  `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §7 (input screening, entitlement gate, tool
  schema validation, allergen fidelity, groundedness, PII redaction, responsible-gaming
  policy) — no embedded rules engine (OPA, `durable_rules`, etc.) and no network hop to
  a policy sidecar.
- **Rationale**: Constitution Principle I and the "Determinism First" development
  standard already settled this question for the conflict checker in feature 005 ("an
  LLM gets travel-time arithmetic right ~95% of the time; a function gets it right 100%
  of the time, in 2ms, and is unit-testable") — the same argument applies to policy
  rules, which have exactly one correct answer per input and must run inside the same
  sub-1.5s deterministic-path budget as everything else on the entitlement-gate/
  preflight path. An external rules engine adds a network hop, a second deployment
  artifact, and a non-Python audit trail for no accuracy benefit on rules this
  well-defined.
- **Alternatives considered**: An OPA sidecar (rejected — infra/latency cost,
  inconsistent with the project's single-process-per-service topology); embedding rules
  as data (e.g., YAML rule tables interpreted generically) — rejected for the
  safety-critical rules (allergen fidelity, self-exclusion) specifically, since a
  data-driven interpreter reintroduces exactly the "a rule that can be undone by a
  prompt/config edit is not enforced" risk Principle IV warns about; reserved instead for
  the one genuinely data-shaped piece, jurisdiction message text (see Smaller Decisions).

Full architecture and per-layer attachment points: ADR-028.

## Decision: allergen-fidelity and self-exclusion blocks route around `HUMAN_APPROVAL`, not through it

- **Decision**: A structurally non-overridable policy block (allergen-fidelity mismatch,
  self-exclusion) never reaches `HUMAN_APPROVAL`/dining's own `human_approval` node — it
  routes to the existing `BLOCKED` node instead, ending that turn with a clear message,
  while a `PolicyFlag(disposition="blocked")` and a `SagaEvent` record the reason for
  later human review. `HUMAN_APPROVAL`'s approve/decline/modify contract is unchanged.
- **Rationale**: `agents/dining/edges/conditions.py`'s existing `human_approval` node
  already includes `dietary_conflicts` in its `interrupt()` payload and accepts
  `{"approved": true}` unconditionally — meaning today a human *can* approve straight
  past a blocking allergen conflict. The spec's own clarification session settled that
  this must never be possible (FR-006). Teaching the *same* approve-capable node to
  special-case one reason and not others would put the safety property behind a runtime
  branch a future change could silently weaken; routing the block to a *different* node
  that has no "approved" outcome at all makes the guarantee structural, matching
  Principle IV's "a rule that can be undone by a prompt edit is not enforced" — the
  analogous claim for code: a rule that depends on an if-branch inside an
  otherwise-permissive node is one accidental refactor from being undone.
- **Alternatives considered**: Add an `overridable: bool` flag to the interrupt payload
  and check it before honoring `approved` (rejected — the check is still just a runtime
  branch inside the same permissive node, the exact fragility above); introduce a
  brand-new interrupt-based node dedicated to blocks (rejected — `BLOCKED` already exists
  for precisely "this request cannot proceed as policy stands," reserved since feature
  004 and unused; a second node would duplicate it without adding anything).

Full routing changes per domain and per the cross-domain path: ADR-029.

## Decision: self-exclusion enforcement topology and `OFFER_AGENT` mounting

- **Decision**: A single `require_gaming_clear(guest_id)` policy function wraps
  `GamingTools.check_rg_status` and is called from two places: (a) the start of
  `agents/gaming/nodes/lookup.py`'s dispatch, before *any* of the three intent branches
  (not only `rg_status`), and (b) the first line of the newly-compiled `OFFER_AGENT`
  node, every turn, regardless of topic. `OFFER_AGENT` is mounted this feature (resolving
  ADR-020's deferral) but scoped narrowly: its only data source is the already-real,
  already-gated `GamingTools.get_loyalty_status` composite call (which already returns
  non-gaming comp offers only, already respecting 403→`POLICY_BLOCKED` at the gateway).
  Building a genuine offer-recommendation/feature-store integration is explicitly
  out of scope — the roster bullet is "offer-registry restriction," not a
  recommendation engine, and spec.md's own Assumptions defer that scope.
- **Rationale**: this satisfies the constitution's two hardest gaming rules in one
  mechanism — self-exclusion gates every gaming tool call *and* the Offer Agent, and the
  Offer Agent structurally cannot emit a gaming-linked offer because no such tool is in
  its registry (mirrors the read-only gaming guarantee's own enforcement style: absence
  of capability, not a discouraging instruction). ADR-020 explicitly named this feature
  as the one expected to supply both the precondition and `OFFER_AGENT`'s mounting.
- **Alternatives considered**: Enforce self-exclusion in MCP gateway middleware, globally
  per-domain (rejected as the primary mechanism — the Offer Agent's tool isn't in the
  `gaming` domain, so a domain-scoped gateway check wouldn't cover it; the check belongs
  in the Policy Engine, called explicitly at both sites); build a real offer/feature-store
  integration now (rejected — unbounded scope increase with no roster mandate, tracked as
  a phase handoff open question instead).

Full topology and the tool-registry exclusion test: ADR-030.

## Decision: PII redaction via Presidio, invoked at the trace-write boundary

- **Decision**: Add `presidio-analyzer`, `presidio-anonymizer`, and `spacy` with the
  small English model (`en_core_web_sm`, vendored into the Docker image at build time —
  no runtime download, preserving the "everything runs offline except Azure OpenAI"
  constraint) as new dependencies. Implement redaction as one function in a new
  `observability/redaction.py` module (co-located with the frozen `attributes.py`, same
  single-source-of-truth convention), called at the boundary where a span/log payload is
  about to be written, and used to finally implement the long-stubbed
  `mcp/gateway/_shared/errors.py:147` `redact()` hook. Structured fields already declared
  as typed/enum on `DietaryRequirement`/`PolicyFlag`/`ToolFailure` are excluded from the
  NLP pass by an explicit field allowlist, not swept up with free text — matching the
  spec clarification that a redacted, structured restriction type/severity must survive
  for allergen-block investigation.
- **Rationale**: the phase roster names Presidio explicitly, and it is the standard
  NER-backed redaction approach for exactly this shape of problem (free-text PII in
  unstructured guest messages) — a regex-only approach would miss names and
  address-like text with materially worse recall, and a cloud PII API would violate the
  project's offline-by-default constraint.
- **Alternatives considered**: regex/keyword-only redaction (rejected — insufficient
  recall on names and free-text disclosures, the exact failure mode Presidio's NER
  backend exists to catch); a cloud-hosted PII redaction API (rejected — violates the
  offline constraint, and adds a network dependency to every trace write, which is far
  too hot a path for an external call).

Full pipeline shape and the field-level allowlist: ADR-031, `contracts/redaction-contract.md`.

## Decision: prompt-injection screening is corpus/embedding-based, not LLM-judged

- **Decision**: Retrieved RAG content is screened by (1) exact/fuzzy match against a
  maintained corpus of known injection strings and (2) embedding-similarity comparison
  using the already-present `sentence-transformers` dependency (no new dependency) — no
  LLM call participates in the screening decision itself. A chunk flagged as suspect is
  excluded from the generation context. Independently, and as a defense-in-depth
  structural guarantee (not merely a screening-accuracy claim): retrieved-content strings
  are verified, by a new structural test mirroring gaming's own no-mutation-verb test, to
  never be interpolated into any prompt that has tool-calling ability — tool selection is
  driven by intent-parser output and state, never by retrieved document text, so an
  injected instruction has no channel to reach a tool call regardless of screening
  recall.
- **Rationale**: Constitution Principle I/Determinism-First applies here too — a
  classifier that is itself an LLM call both re-introduces the exact risk being screened
  for (a second model in the loop that could itself be manipulated) and can't give
  FR-008's absolute guarantee ("MUST NOT be able to alter which tool is called"); a
  structural, architecture-level guarantee can. The red-team corpus lives alongside the
  existing `rag/eval/adversarial_set.yaml` (cross-tenant probes, feature 002) as a
  sibling adversarial-test artifact, not a new test framework.
- **Alternatives considered**: an LLM-based injection classifier (rejected — determinism
  and the second-model-in-the-loop risk above); relying on screening accuracy alone
  without the structural tool-call-isolation guarantee (rejected — screening is
  necessarily probabilistic against novel phrasings; the absolute FR-008 guarantee needs
  an architectural backstop, not just a better classifier).

Full corpus format and the structural test: ADR-032.

---

## Decisions 033-037

Five further decisions, initially filed below as "smaller, no ADR needed," are — per
project direction to record everything at full weight for this phase — now each their
own ADR rather than a research-note paragraph:

- **ADR-033**: `hypothesis` adopted as a new dev dependency for the policy module's
  property-based tests (dietary-fidelity comparison, self-exclusion gating,
  redaction-allowlist enforcement), additive to — not a replacement for — the
  hand-authored adversarial scripts (40-turn self-exclusion conversation, injection
  corpus).
- **ADR-034**: jurisdiction-mandated message text is static per-tenant, per-trigger data
  (`policy/jurisdiction.py`'s lookup table), looked up deterministically — explicitly
  distinguished from the "no rules as data" position ADR-028 took, since a lookup table
  with no evaluation semantics of its own is not a rules engine.
- **ADR-035**: no new storage. The policy audit trail reuses `policy_flags` (a
  compliance determination) plus `SagaEvent`/`saga_log` (the item-level narrative),
  mirroring ADR-027's existing "reuse the mirror, don't add a table" pattern from one
  feature earlier.
- **ADR-036**: distress-signal detection reuses ADR-032's exact corpus/embedding-
  similarity technique against a second, distress-specific corpus — a routine
  application of an already-justified deterministic screening approach, not a new one,
  tuned so the ambiguous band still escalates (FR-018).
- **ADR-037**: self-exclusion enforcement and `OFFER_AGENT` reuse the existing
  `GamingTools`/`McpGamingTools` client (`check_rg_status`, `get_loyalty_status`)
  exactly as built in feature 005 — zero new MCP tool, gateway module, or sim endpoint;
  `GamingTools`'s own docstring already forbids a fourth method.
