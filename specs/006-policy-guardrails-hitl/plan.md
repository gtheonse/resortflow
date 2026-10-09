# Implementation Plan: Policy, Guardrails & HITL

**Branch**: `006-policy-guardrails-hitl` | **Date**: 2026-09-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-policy-guardrails-hitl/spec.md`

## Summary

Build the parts of ResortFlow that make it deployable in a regulated gaming jurisdiction: a
real `policy/` module (Track G, currently empty) enforcing jurisdiction-aware
responsible-gaming rules, allergen fidelity, prompt-injection screening, and PII
redaction — plus mounting the `OFFER_AGENT` node ADR-020 reserved specifically pending
this Policy Engine. Research surfaced that most of the underlying machinery already
exists and is unused: `check_rg_status` is already a real, gateway-wired vendor call;
`PolicyFlag`/`ResortFlowState.policy_flags` have been reserved since the frozen state contract
in feature 001 with zero writers; `BLOCKED`, `POLICY_PREFLIGHT`, `POLICY_POSTFLIGHT`, and
`OFFER_AGENT` are reserved node names with no real bodies. This feature's job is
concentrated in **closing two concrete, already-identified gaps** — `agents/gaming/nodes/
lookup.py` only checks self-exclusion for one of three intents, and
`agents/dining/edges/conditions.py`'s `human_approval` node can currently be resumed with
`{"approved": true}` straight past a blocking allergen conflict — and in **activating
reserved fields/nodes** rather than inventing new state or a new subsystem. Five
decisions met the significance bar; five more were promoted to full ADRs at this
phase's explicit direction. All ten (028-037) are recorded in `docs/adr/`.

## Technical Context

None of the following carries `NEEDS CLARIFICATION` — this feature's stack is fixed
project-wide (`AGENTS.md`) except for two explicitly-scoped additions (Presidio, a
red-team corpus format) resolved in `research.md`; nothing here required guessing.

**Language/Version**: Python 3.11+ (matches `agents/`, `graph/`, `mcp/gateway/`,
unchanged)

**Primary Dependencies**: LangGraph (existing — no new node names, only real bodies for
already-reserved ones), Pydantic v2 (`graph.models.PolicyFlag`, reused unmodified),
`GamingTools`/`McpGamingTools` (existing, reused unmodified as the self-exclusion data
source). New this feature: `presidio-analyzer` + `presidio-anonymizer` + `spacy` with
`en_core_web_sm` (PII redaction, ADR-031); `sentence-transformers` (already a dependency,
reused for injection/distress similarity scoring, no new ML dependency for that layer).

**Storage**: PostgreSQL, unchanged — no new table. The policy audit trail reuses
`policy_flags` (existing field, gains an `append_only` reducer — ADR-029) and
`saga_log` (existing, unchanged), mirroring ADR-027's "reuse the existing mirror, don't
add a table" precedent.

**Testing**: pytest, following the existing per-domain `tests/`/`eval/` pattern; new
`hypothesis` dev-dependency for the adversarial/property-style checks this phase's exit
criteria require (40-turn self-exclusion script, injection-corpus sweep, redaction-
completeness) — the same "mandatory adversarial test category" reasoning the constitution
already applies to saga permutations and tenant isolation, extended here.

**Target Platform**: Linux server (docker-compose locally; Azure in the eventual
production target), unchanged. Constraint carried forward explicitly: Presidio's spaCy
model is vendored into the Docker image at build time (Track F, `docker/`), never
fetched at runtime — preserving "everything runs offline except Azure OpenAI."

**Project Type**: Multi-service backend, unchanged. This is the first feature that
populates Track G (`policy/`) with real code.

**Performance Goals**: Inherits Constitution Principle I's existing 1.5s deterministic-
path bound. `POLICY_PREFLIGHT`/gaming's self-exclusion check add one vendor call
(`check_rg_status`, already on the existing hot path for the `rg_status` intent — this
feature adds the same call to two more intents, not a new call shape) and one local,
no-network embedding-similarity comparison (injection/distress screening) — no new
network hop is introduced anywhere in this feature.

**Constraints**: Per `docs/models.md`'s `[CRIT]` never-economy list, "Policy engine,
responsible gaming, self-exclusion" and "Allergen fidelity check" are both named for this
phase — moot in practice, since every new code path this feature adds is deterministic
Python (ADR-028) with zero new LLM call sites; distress-signal detection is explicitly
required to involve no model judgment either (constitution, FR-004), so Principle VI's
runtime-tier question does not arise for this feature's new logic.

**Scale/Scope**: See Project Structure below for the exact file list. No new MCP domain,
no new sim, no new PostgreSQL schema.

## Constitution Check

*Gate: MUST pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Ambiguity-Based Routing | **PASS** | Zero new LLM call sites this feature (ADR-028). Every new check (dietary fidelity, self-exclusion, injection screening, distress detection) is deterministic — exactly the "arithmetic, not judgment" case this principle reserves for non-agentic code. |
| II. Governed Integration Surface | **PASS** | No new vendor SDK, no new MCP domain. `check_rg_status`/`get_loyalty_status` are already MCP-mediated, tenant-injected server-side, unchanged by this feature. `OFFER_AGENT` gains no tool beyond the existing `get_loyalty_status`, and the gaming domain's read-only guarantee (absence of any mutating method) is unaffected. |
| III. Two-Phase Mutation | **PASS, with required work** | This feature does not add a new mutation path, but it corrects an existing one: dining's `human_approval` currently allows resuming `{"approved": true}` past a blocking allergen conflict, which — if ever exercised — would let an irreversible `CONFIRM` proceed against an unsafe structured record. ADR-029 closes this by routing the non-overridable case to `BLOCKED` before it ever reaches the approval interrupt, restoring the "approval sits exactly at the reversible/irreversible boundary, and only for genuinely approvable decisions" property this principle requires. |
| IV. Policy Is Code, Not Prompt | **PASS — this feature's entire purpose** | Every hard rule this principle states (structured dietary enum with post-flight fidelity check; self-exclusion before any gaming tool and before the Offer Agent, every turn; no gaming-promotion initiation; deterministic jurisdiction messaging; distress routes to human with no model judgment) is this feature's literal scope, per ADRs 028-030 and `contracts/policy-engine-contract.md`. |
| V. Durable, Inspectable State | **PASS** | `policy_flags`'s `append_only` reducer amendment (ADR-029) is exactly this principle's "every graph step checkpointed... inspectable" requirement, closing a gap where the field existed but nothing durably accumulated into it. `redact_for_trace` (ADR-031) is the redaction half of this principle's own "checkpoints must be encrypted... dietary requirements are health data" concern, applied to the separate, lower-trust observability trail rather than the encrypted checkpoint store itself. |
| VI. Declared Model Tier | **PASS — trivially, no new LLM node** | No new LLM-invoking node exists in this feature (see Constraints above), so no runtime tier declaration is needed. Existing FRONTIER-tier nodes (planner, recovery, groundedness judge) are untouched. |

No Constitution violation requires a Complexity Tracking entry — see below.

## Project Structure

### Documentation (this feature)

```
specs/006-policy-guardrails-hitl/
├── plan.md              # this file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/            # Phase 1 output
│   ├── policy-engine-contract.md
│   ├── state-schema-amendment-006.md
│   ├── graph-topology-v006.md
│   ├── offer-agent-tool-registry-contract.md
│   └── redaction-contract.md
└── tasks.md              # /speckit-tasks (not yet generated)
```

### Source Code (repository root)

```
policy/                             # NEW — Track G, empty since feature 001
├── __init__.py
├── types.py                        # shared internal NamedTuples (data-model.md):
│                                   #   GamingClearance, DietaryFidelityResult,
│                                   #   InjectionScreeningResult, DistressCheck
├── gaming.py                       # require_gaming_clear (ADR-030)
├── dietary.py                      # check_dietary_fidelity (ADR-029)
├── injection.py                    # screen_retrieved_content (ADR-032)
├── distress.py                     # check_distress_signal
├── jurisdiction.py                 # mandated_message_for
└── tests/                          # NEW — unit tests per module above

observability/
├── attributes.py                   # UNCHANGED
└── redaction.py                    # NEW — redact_for_trace (ADR-031)

graph/
├── nodes.py                        # UNCHANGED — no new/renamed node names (all 5 already reserved)
├── supervisor.py                   # AMENDED — real make_policy_preflight_node,
│                                   #   make_policy_postflight_node (new), real
│                                   #   _route_from_policy_preflight, new OFFER_AGENT
│                                   #   node + mount; make_human_approval_node UNCHANGED
├── state.py                        # AMENDED (additive-only) — policy_flags reducer (ADR-029)
└── models.py                       # UNCHANGED — PolicyFlag already fully modeled

agents/
├── dining/
│   ├── edges/conditions.py         # AMENDED — routing split: blocking dietary-fidelity
│   │                               #   → BLOCKED; ordinary approval → human_approval (unchanged)
│   └── nodes/allergen_check.py     # AMENDED — adds check_dietary_fidelity call alongside
│                                   #   existing DietaryConflict menu-matching (unchanged)
├── gaming/
│   ├── nodes/lookup.py             # AMENDED — require_gaming_clear before all 3 intents
│   └── graph.py                    # UNCHANGED — ALL_NODE_NAMES unchanged, no new node
├── entertainment/, stay/, wellness/  # UNCHANGED this feature (own retrieval call sites
│                                     #   gain screen_retrieved_content — see below — but
│                                     #   no other change)
└── {each domain}/menu.py or equivalent retrieval call site
                                     # AMENDED — screen_retrieved_content applied to
                                     #   chunks before they enter a generation context (ADR-032)

mcp/gateway/_shared/errors.py        # AMENDED — redact() stub (line 147) replaced by a
                                     #   real call into observability.redaction

content/
└── redteam/injection_corpus.yaml    # NEW — sibling to rag/eval/adversarial_set.yaml

docker/                              # AMENDED — spaCy model vendored into image build
                                      #   (ADR-031's offline constraint)

pyproject.toml                       # AMENDED — presidio-analyzer, presidio-anonymizer,
                                      #   spacy, hypothesis (dev) added
```

**Structure Decision**: `policy/` (Track G) is this feature's primary, new surface —
the first code Track G has ever owned. `graph/` and `agents/` (Track C) carry the
required amendments closing the two concrete gaps this plan identified; `observability/`
gains one new module (`redaction.py`) rather than becoming a new track. `mcp/gateway/`
(Track B) has exactly one line changed (the `redact()` stub) and gains no new tool, no
new domain. `content/` (Track D) gains one new fixture file, sibling to an existing
pattern. `docker/`/`pyproject.toml` (Track F) carry the new dependencies. No directory
outside this set is touched — in particular, no new MCP domain module and no new
`sims/` endpoint are added; both existing gaming endpoints this feature depends on
(`check_rg_status`, `get_loyalty_status`) are already real (research.md Discovery A).

## Complexity Tracking

*No entries.* Every Constitution gate above passed; Principle III's "required work" row
is a correction this feature makes to an existing gap, not a violation this plan is
asking to excuse. No new project, no new service, no structure beyond the existing
seven-track layout is introduced.

## Architecture Decision Records *(mandatory — ResortFlow project override)*

Ten decisions in this plan are recorded as ADRs — the five that meet the strict
significance bar (constrains a later feature; a competent engineer could reasonably have
chosen differently; reversing it later would be breaking; or it trades one stated
project value against another), plus five more that would ordinarily be filed as
routine research-note decisions but are promoted to full ADRs at this phase's explicit
direction, since this phase's compliance
surface warrants recording every decision at full weight rather than triaging some out.
Each is fully drafted at `docs/adr/`:

### Plan completion gate

| ADR | Decision | Status |
|---|---|---|
| [028](../../docs/adr/028-policy-engine-architecture.md) | `policy/` is plain deterministic Python, organized by the seven-layer enforcement table — no rules engine, no sidecar | [x] drafted |
| [029](../../docs/adr/029-policy-block-routing-and-flag-reducer.md) | Non-overridable policy blocks (allergen fidelity, self-exclusion) route to the existing `BLOCKED` node, never `HUMAN_APPROVAL`; `policy_flags` gains an `append_only` reducer | [x] drafted |
| [030](../../docs/adr/030-self-exclusion-topology-offer-agent.md) | One `require_gaming_clear` function gates all three gaming-lookup intents and `OFFER_AGENT`; `OFFER_AGENT` is mounted this feature scoped to the existing `get_loyalty_status` call only | [x] drafted |
| [031](../../docs/adr/031-pii-redaction-presidio.md) | PII redaction via Presidio + local spaCy, one function at the trace-write boundary, structured dietary fields exempted by an explicit allowlist | [x] drafted |
| [032](../../docs/adr/032-injection-screening-approach.md) | Injection screening is deterministic corpus/embedding match, backed by a tested structural guarantee that retrieved content never feeds tool-selection logic | [x] drafted |
| [033](../../docs/adr/033-hypothesis-adversarial-testing.md) | `hypothesis` adopted for the policy module's property-based tests, additive to the hand-authored adversarial scripts | [x] drafted |
| [034](../../docs/adr/034-jurisdiction-message-static-data.md) | Jurisdiction-mandated message text is static per-tenant, per-trigger lookup data, not evaluated logic — distinguished from ADR-028's rules-engine rejection | [x] drafted |
| [035](../../docs/adr/035-policy-audit-trail-reuses-existing-storage.md) | No new storage — the policy audit trail reuses `policy_flags` + `saga_log`, mirroring ADR-027's existing reuse pattern | [x] drafted |
| [036](../../docs/adr/036-distress-signal-detection-approach.md) | Distress-signal detection reuses ADR-032's corpus/embedding technique against a second corpus — no model judgment, ambiguous cases escalate | [x] drafted |
| [037](../../docs/adr/037-reuse-existing-gaming-tools-no-new-mcp-surface.md) | Self-exclusion enforcement and `OFFER_AGENT` reuse the existing `GamingTools`/`McpGamingTools` client verbatim — zero new MCP tool, gateway module, or sim endpoint | [x] drafted |

Every ADR above records its context, options, decision, and consequences.
