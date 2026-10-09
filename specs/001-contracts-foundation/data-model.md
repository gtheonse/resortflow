# Data Model: Contracts Foundation

**Feature**: `001-contracts-foundation` | **Date**: 2026-09-09

Phase 1 output. Entity definitions, validation rules, relationships and state transitions.
Enum values are transcribed from the frozen specifications in `docs/contracts/openapi/` and
are authoritative — where this document and a vendor specification disagree, the
specification wins and this document is a defect.

---

## Conventions

- **Sensitivity markers.** Fields are annotated `PII` or `HEALTH` per FR-023. The marker is
  carried as `Annotated` metadata on the field so redaction and encryption enumerate it from
  the model rather than from a maintained list.
- **Optionality.** `str | None` means the value is genuinely unknown at some point in the
  lifecycle. It is never used to mean "not bothered to populate".
- All models set `extra="forbid"` — an unexpected key is a contract violation, not a
  tolerated extra.

---

## Enumerations

### `DietaryRestriction`

Transcribed verbatim from `dining-sim.openapi.yaml`. 14 values:

`shellfish`, `tree_nut`, `peanut`, `dairy`, `gluten`, `egg`, `soy`, `sesame`, `fish`,
`vegetarian`, `vegan`, `halal`, `kosher`, `low_sodium`

### `DietarySeverity`

`preference` | `intolerance` | `allergy_moderate` | `allergy_severe`

`allergy_severe` must reach the kitchen as an allergen alert, not a preference note.

### `Domain`

`dining` | `entertainment` | `stay` | `wellness` | `gaming`

`gaming` is read-only by construction — no mutating tool for it exists in any registry.

Each value is the eventual binding point for one domain subgraph named in §4 of
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`, and each subgraph integrates via one simulated
vendor surface named in §1 and §9 of the same document. Neither the subgraphs nor the
simulators are built by this feature — see spec.md → Assumptions — but the mapping is
recorded here because it is exactly what a downstream track needs to confirm it is binding
to the right thing:

| `Domain` value | Owning subgraph (§4) | Simulated vendor surface (§1, §9) |
|---|---|---|
| `dining` | Dining subgraph | `dining-sim` — SevenRooms-equivalent reservation platform |
| `entertainment` | Entertainment subgraph | `ticketing-sim` — Ticketmaster-equivalent event platform |
| `stay` | Stay subgraph | `pms-sim` — Opera-equivalent property management system |
| `wellness` | Wellness subgraph | `amenities-sim` — spa/fitness/cabana/golf scheduler; source material names no specific real-world vendor |
| `gaming` | Gaming & Loyalty subgraph (read-only) | `casino-sim` — casino management system; source material names no specific real-world vendor |

### `HoldStatus`

`pending` | `held` | `confirmed` | `released` | `expired` | `orphan_released`

### `ErrorClass`

`RETRYABLE` | `TERMINAL` | `REQUIRES_HUMAN` | `POLICY_BLOCKED`

---

## Hold state machine

```
                  ┌──────────────────────── vendor never answered ────────┐
                  │                                                       ▼
   [start] ──▶ pending ──── vendor confirms hold ────▶ held ─────▶ orphan_released ✦
                  │                                    │
                  │ vendor rejects                     ├── commit succeeds ──▶ confirmed ✦
                  ▼                                    ├── TTL elapses ──────▶ expired   ✦
              released ✦                               └── deliberate release ▶ released ✦

   ✦ = terminal.  Terminal states never regress to pending or held.
```

**Invariant enforced by `merge_holds`**: given two versions of the same hold (same
`idempotency_key`), if either is terminal, the terminal one wins regardless of arrival
order. Resume replays steps, so arrival order carries no information.

**Re-acquisition is not a transition.** A guest whose hold expired and who is offered a new
time produces a **new** `Hold` with a **new** `idempotency_key`. The expired row is never
revived.

---

## Graph Node Name Registry

**Permanent home**: `graph/nodes.py` — string constants only. No node behaviour is defined
by this feature; feature 005 (multi-agent-orchestration) implements every node listed here
against the exact name reserved for it.

**Why this is a Phase 0 contract, not a Phase 5 one.** AGENTS.md's hard rules state a node
name is never renamed in a patch release, and the constitution's CONTRACT STABILITY
standard treats it the same way. A node name is not cosmetic: LangGraph's checkpoint schema
keys `checkpoint_ns` and each row's `task_path` by node identity, so a rename after
checkpoints exist in production is a persistence-format break, not a refactor. Concretely,
this feature's own `telemetry-attributes.md` already declares `node` a bounded metric
dimension — freezing the registry here is what the word "bounded" was referring to; without
it, that claim had nothing to point at.

Transcribed from the `stateDiagram-v2` in §4 of `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`,
reconciled against that section's agent-responsibility table:

| Node name | Role | Source |
|---|---|---|
| `intent_router` | Classifies deterministic vs. agentic; detects domain set | Both sources |
| `deterministic` | The non-agentic fallback path (~60% of traffic) | Diagram |
| `supervisor` | Owns routing, fan-out/fan-in, the replan loop | Both sources |
| `policy_preflight` | Entitlement, jurisdiction, self-exclusion gate | Both sources |
| `blocked` | Terminal path for a preflight violation | Diagram |
| `planner` | Decomposes intent into an ordered/parallel task list | Both sources |
| `dining` | Domain subgraph — venue discovery through hold/confirm | Both sources |
| `entertainment` | Domain subgraph — event discovery through hold/purchase | Both sources |
| `stay` | Domain subgraph — room ops, checkout timing | Both sources |
| `wellness` | Domain subgraph — spa, fitness, cabana, golf | Both sources |
| `gaming` | Domain subgraph, **read-only tools only** | Both sources |
| `conflict_check` | Deterministic — no LLM. Travel-time and party-size feasibility | Both sources |
| `proposal_assembly` | Assembles the coherent multi-domain proposal for approval | Diagram |
| `human_approval` | The `interrupt()` boundary — reversible/irreversible line | Diagram |
| `commit` | Sequential, riskiest-first external mutation | Diagram |
| `release` | Declined-approval path — releases held inventory | Diagram |
| `commit_verify` | Confirms each commit landed; detects partial failure | Diagram |
| `recovery` | Semantic compensation on partial failure | Both sources |
| `policy_postflight` | Groundedness, PII, allergen-fidelity, RG compliance | Both sources |
| `respond` | Terminal — returns to the guest | Diagram |
| `offer_agent` *(reserved)* | Contextual upsell from the Snowflake feature store | Roster table only — **absent from the diagram; see note below** |
| `human_handoff` *(reserved)* | Escalation to a live concierge with full state snapshot | Roster table only — **shown in the diagram only implicitly, inside `human_approval`** |

**Unresolved source inconsistency, stated rather than silently settled.** The roster table
lists `offer_agent` and `human_handoff` as first-class nodes; the diagram does not draw
either one as a distinct state. Both names are reserved so that neither Track C nor Track G
has to invent one later, but whether each becomes its own graph node or is folded into an
adjacent one (`offer_agent` into `proposal_assembly`; `human_handoff` into a branch of
`human_approval`) is explicitly **not decided by this feature** and is called out as an open
question for feature 005's plan.


| Field | Type | Notes |
|---|---|---|
| `guest_id` | `str` | Stable identifier |
| `display_name` | `str` | **PII** |
| `phone` | `str \| None` | **PII** |
| `email` | `str \| None` | **PII** |
| `loyalty_tier` | `str \| None` | Bounded; safe as a metric dimension |
| `entitlements` | `list[str]` | Gates what may be offered |

### `Stay`

| Field | Type | Notes |
|---|---|---|
| `stay_id` | `str` | |
| `guest_id` | `str` | → `Guest` |
| `tenant_id` | `str` | `MGV` or `MHB` |
| `arrival` / `departure` | `date` | Bounds every date a request may address |
| `room_id` | `str \| None` | Null before assignment |

**Validation**: `departure > arrival`.

### `DietaryRequirement`

| Field | Type | Notes |
|---|---|---|
| `restriction` | `DietaryRestriction` | Required. **HEALTH** |
| `severity` | `DietarySeverity` | Required. **HEALTH** |
| `applies_to_guest` | `str \| None` | e.g. "guest 2 of 2". **PII** |
| `detail` | `str \| None` | Free text. **HEALTH** |

**Validation**: `restriction` and `severity` are both non-optional. `detail` may accompany
them and can never substitute for them — this is the structural expression of the
constitution's rule that a restriction is never carried in free text alone.

### `Proposal`

| Field | Type | Notes |
|---|---|---|
| `proposal_id` | `str` | **Merge key for `merge_proposals`** |
| `domain` | `Domain` | |
| `tenant_id` | `str` | |
| `title` | `str` | Guest-facing |
| `starts_at` / `ends_at` | `datetime \| None` | Feeds the deterministic conflict checker |
| `location_zone` | `str \| None` | Travel-time input |
| `party_size` | `int \| None` | |
| `price` | `Decimal \| None` | |
| `hold_idempotency_key` | `str \| None` | → `Hold`, when one has been placed |
| `dietary` | `list[DietaryRequirement]` | |

### `Hold`

| Field | Type | Notes |
|---|---|---|
| `idempotency_key` | `str` | **Required. Merge key for `merge_holds`.** Exists before the vendor responds |
| `hold_id` | `str \| None` | Vendor-assigned; null until the vendor answers |
| `domain` | `Domain` | |
| `tenant_id` | `str` | |
| `saga_id` | `str` | → ledger rows |
| `status` | `HoldStatus` | |
| `expires_at` | `datetime \| None` | Null while `pending` |
| `proposal_id` | `str \| None` | → `Proposal` |

**Validation**: `idempotency_key` is non-optional — a hold that cannot be joined across a
crash window is not a hold. `expires_at` must be present once `status` is `held`.

### `Confirmation`

| Field | Type | Notes |
|---|---|---|
| `confirmation_id` | `str` | Vendor-issued |
| `idempotency_key` | `str` | → the `Hold` it was promoted from |
| `domain` | `Domain` | |
| `confirmed_at` | `datetime` | |
| `cancellation_deadline` | `datetime \| None` | |

Append-only in state. A confirmation is never rewritten.

### `SagaEvent`

| Field | Type | Notes |
|---|---|---|
| `event_id` | `str` | |
| `saga_id` | `str` | Spans multiple turns and multiple traces |
| `sequence` | `int` | Monotonic within a saga |
| `kind` | `str` | `hold_placed`, `commit_attempted`, `commit_failed`, `compensated`, … |
| `domain` | `Domain \| None` | |
| `occurred_at` | `datetime` | |
| `detail` | `dict[str, str]` | Bounded scalars only — no nested payloads |

Append-only, and mirrored to Postgres so compensation survives process death.

### `Plan` and `PlanTask`

| `Plan` field | Type | Notes |
|---|---|---|
| `plan_id` | `str` | |
| `tasks` | `list[PlanTask]` | |
| `created_at` | `datetime` | |

| `PlanTask` field | Type | Notes |
|---|---|---|
| `task_id` | `str` | |
| `domain` | `Domain` | |
| `depends_on` | `list[str]` | Task ids; expresses the fan-out/sequence boundary |
| `constraints` | `dict[str, str]` | e.g. dinner ends ≥45 min before curtain |

### `PolicyFlag`

| Field | Type | Notes |
|---|---|---|
| `flag_id` | `str` | |
| `rule` | `str` | Which rule fired |
| `disposition` | `str` | `blocked`, `redacted`, `escalated`, `messaged` |
| `jurisdiction` | `str \| None` | `NV` or `MA` |
| `raised_at` | `datetime` | |

### `ToolFailure`

| Field | Type | Notes |
|---|---|---|
| `domain` | `Domain` | |
| `tool` | `str` | |
| `error_class` | `ErrorClass` | |
| `vendor_code` | `str \| None` | Null for status-only vendors |
| `http_status` | `int \| None` | |
| `retry_after_seconds` | `int \| None` | |
| `detail` | `str` | Redacted before persistence |

### `Chunk` and `Citation`

| `Chunk` field | Type | Notes |
|---|---|---|
| `chunk_id` | `str` | **Dedupe key for `dedupe_by_id`** |
| `tenant_id` | `str` | Isolation is enforced at query time, never by the model |
| `content` | `str` | |
| `source_uri` | `str` | |
| `valid_from` / `valid_to` | `datetime \| None` | Stale-content filter |
| `content_hash` | `str` | |

`Citation` carries `chunk_id`, `source_uri` and an optional quoted span.

### `LedgerEntry`

Not part of `ResortFlowState` — this is the durable row, defined here because it is a frozen
contract. See `contracts/hold-ledger.sql` for the DDL.

| Field | Type | Notes |
|---|---|---|
| `ledger_id` | `bigserial` | |
| `saga_id`, `session_id`, `tenant_id` | `text` | |
| `domain` | `text` | |
| `idempotency_key` | `text` | **UNIQUE** — the join key across every crash window |
| `hold_id` | `text \| null` | Null until the vendor responds |
| `expires_at` | `timestamptz \| null` | |
| `status` | `text` | Constrained to `HoldStatus` values |
| `trace_id` | `text \| null` | Correlates to telemetry |
| `created_at` / `updated_at` | `timestamptz` | |

---

## `ResortFlowState` — the frozen key set

Twenty keys. `test_state_contract.py` asserts this set literally, so any later feature that
edits it fails CI rather than drifting silently.

| Key | Reducer | Notes |
|---|---|---|
| `tenant_id`, `guest_id`, `session_id`, `jurisdiction` | none | Set once at entry. Never model-derived |
| `entitlements` | none | |
| `saga_id` | none | **Deviation** — added; see plan.md and ADR-019 |
| `messages` | `add_messages` | Windowing deferred to feature 005 |
| `plan` | none | |
| `replan_count` | none | Hard bound 3, then escalate |
| `proposals` | `merge_proposals` | Merge by `proposal_id` |
| `holds` | `merge_holds` | Merge by `idempotency_key`, terminal-sticky |
| `confirmations` | `append_only` | |
| `failures` | `append_only` | |
| `retrieved` | `dedupe_by_id` | First-seen wins, preserving RRF rank |
| `citations` | none | |
| `awaiting_approval`, `approval_scope` | none | |
| `policy_flags` | none | |
| `escalate` | none | |
| `saga_log` | `append_only` | Mirrored to Postgres |

---

## Error taxonomy mapping

The five vendors expose **three distinct error envelope shapes**, and only two of the five
publish a code enumeration at all. This is the concrete justification for principle II —
normalisation has to happen somewhere, and that somewhere is the MCP boundary.

| Vendor | Envelope | Codes published? |
|---|---|---|
| dining | `{ "error": { code, detail, retryable } }` | Yes — 7 codes |
| ticketing | `{ "fault": { type, message, detail, retry_after_seconds } }` | Yes — 7 codes |
| amenities | `{ "err_code", "err_msg" }` — flat, a third shape | No — HTTP status only |
| pms | `{ "RESPONSE": { "STATUS", "DATA" } }` — legacy wrapper | No — HTTP status only |
| casino | Bare HTTP status | No |

`classify(vendor_code, http_status, retryable_hint) -> ErrorClass` therefore takes both a
code and a status, because for three of five vendors the status is all there is.

### Published codes

| Code | Vendor | Class | Why |
|---|---|---|---|
| `SLOT_UNAVAILABLE` | dining | `TERMINAL` | Retrying the same slot cannot succeed; the planner must choose another |
| `INVALID_PARTY_SIZE` | dining | `TERMINAL` | A caller error; identical retry fails identically |
| `VENUE_CLOSED` | dining | `TERMINAL` | |
| `HOLD_EXPIRED` | dining, ticketing | `REQUIRES_HUMAN` | Inventory was lost after the guest saw a plan. Re-acquisition changes what they agreed to |
| `INSIDE_CANCEL_WINDOW` | dining | `REQUIRES_HUMAN` | Money is at stake; a human decides |
| `RATE_LIMITED` / `RATE_LIMIT_EXCEEDED` | dining, ticketing | `RETRYABLE` | Carries `retry_after` |
| `UPSTREAM_ERROR` / `UPSTREAM_TIMEOUT` | dining, ticketing | `RETRYABLE` | Idempotency key makes retry safe |
| `INVENTORY_UNAVAILABLE` | ticketing | `TERMINAL` | |
| `EVENT_CANCELLED` | ticketing | `TERMINAL` | |
| `NON_REFUNDABLE` | ticketing | `REQUIRES_HUMAN` | |
| `SIGNATURE_INVALID` | ticketing | `TERMINAL` | A configuration defect. Retrying masks it |

### Status-only vendors

| Status | Meaning in context | Class |
|---|---|---|
| `403` (casino, offers) | *"Offers suppressed — responsible gaming flag active"* | **`POLICY_BLOCKED`** |
| `409` (amenities, pms) | Practitioner taken / high occupancy | `TERMINAL` |
| `410` (amenities, pms) | Hold expired | `REQUIRES_HUMAN` |
| `429` (any) | Throttled | `RETRYABLE` |
| `5xx` (any) | Upstream fault | `RETRYABLE` |

The casino `403` is worth naming explicitly: it is a real, specified `POLICY_BLOCKED` case
rather than a hypothetical one. It must never classify as retryable — retrying a responsible
gaming suppression is both futile and exactly the behaviour the constitution forbids.

### Fallback

Unknown code with `5xx` → `RETRYABLE`. Unknown code with `4xx` → `TERMINAL`. Never raise: an
unrecognised code from a vendor's minor release must not become a ResortFlow outage.

### System failure modes

The nine rows of §11 of `docs/ResortFlow-HITL-Durability-State.md` map as: crash mid-fan-out and
single-node parallel failure are recovered by pending writes and the ledger rather than
classified; hold expiry during human wait → `REQUIRES_HUMAN`; vendor confirm timeout →
`RETRYABLE` (idempotency key makes it safe); checkpoint write failure → `TERMINAL`, failing
loudly by design; compensation failure → `REQUIRES_HUMAN` with dead-letter and duty-manager
escalation.
