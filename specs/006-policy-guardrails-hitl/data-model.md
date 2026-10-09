# Phase 1 Data Model: Policy, Guardrails & HITL

Per research.md's Discovery B, this feature activates reserved contract fields rather
than adding new ones wherever possible. Each entity below states whether it is
**existing (activated)**, **existing (amended)**, or **new**.

## `PolicyFlag` — existing (amended), `graph/models.py`

spec.md's Key Entities section names a conceptual "Policy Block" (reason, item,
resolution state). This feature realizes it as two existing records together, not a
new type: `PolicyFlag` carries the reason/disposition, and a companion `SagaEvent`
carries which item it applies to — see "State transitions summary" below for how they
pair up per rule.

No field changes. One reducer amendment (ADR-029):

```python
# Before (feature 001, never annotated — last-write-wins channel semantics):
policy_flags: list[PolicyFlag]

# After (this feature):
policy_flags: Annotated[list[PolicyFlag], append_only]
```

**Why this is a real gap, not cosmetic**: an unannotated `list`-typed key in a LangGraph
`TypedDict` state is replaced wholesale by whichever write reaches the channel, not
merged. Feature 005 already runs concurrent domain subgraphs in the same superstep
(fan-out); this feature is the first to actually write `policy_flags` from more than one
place (e.g., a dining allergen flag and a gaming self-exclusion flag could both fire in
the same itinerary's fan-out). Without `append_only`, one write would silently erase the
other — an audit-trail integrity bug, not merely a missed feature. `append_only` is
already imported and used by `confirmations`/`failures`/`saga_log` (`graph/state.py`);
this reuses the existing reducer function, not a new one.

**Existing shape** (unchanged):

| Field | Type | Notes |
|---|---|---|
| `flag_id` | `str` | |
| `rule` | `str` | Which policy rule fired, e.g. `"allergen_fidelity_mismatch"`, `"self_exclusion_active"`, `"injection_detected"`, `"distress_signal"`, `"dependency_unavailable"` (closed vocabulary below) |
| `disposition` | `str` | One of `blocked`, `redacted`, `escalated`, `messaged` (existing docstring vocabulary — this feature is the first to instantiate all four) |
| `jurisdiction` | `str \| None` | `NV` or `MA` |
| `raised_at` | `datetime` | |

**New closed vocabulary for `rule`** (documented here since `rule: str` has no enum in
the frozen model — adding one now would be a breaking type change to a field this
feature doesn't own the freeze on; the vocabulary is enforced by `policy/` code, not the
type system):

- `allergen_fidelity_mismatch` — dietary restriction mentioned in conversation but
  absent from/contradicted by the structured request (FR-006)
- `self_exclusion_active` — guest is self-excluded; gaming content/offer withheld
  (FR-001, FR-003)
- `injection_detected` — retrieved content matched the injection corpus or exceeded the
  similarity threshold (FR-007)
- `distress_signal` — utterance indicated (or was ambiguous for) gambling-related
  distress (FR-004, FR-018)
- `dependency_unavailable` — a policy check's own dependency could not be reached;
  affected capability failed closed (FR-021)
- `jurisdiction_message_gaming_content` / `jurisdiction_message_offer` — a
  jurisdiction-mandated message was appended to a response (FR-012); always paired
  with `disposition="messaged"`, never a block. Added during implementation
  (`make_policy_postflight_node`, `OFFER_AGENT`) as the two concrete triggers named
  in FR-012 — not present in the original closed-vocabulary draft above, which named
  the five blocking/escalating rules but left `messaged`'s own rule string open.

**`disposition` usage per rule**: `allergen_fidelity_mismatch` and
`self_exclusion_active` always pair with `blocked`; a `distress_signal` pairs with
`escalated`; `dependency_unavailable` pairs with `blocked` (the capability) and
`escalated` (the event); `redacted` is used independently of the above, attached wherever
`observability/redaction.py` actually removed content from a record; `messaged` records
that a jurisdiction-mandated message was appended (FR-012), for audit completeness — not
itself a block.

## `ResponsibleGamingStatus` — existing, unchanged, `mcp/gateway/gaming/models.py`

Reused as-is (research.md Discovery A). Fields: `guest_id`, `jurisdiction`,
`self_excluded`, `exclusion_scope`, `limit_flags`, `offers_permitted`,
`gaming_content_permitted`, `mandated_message`, `support_resources`. Already returned by
the real `check_rg_status` MCP tool. This feature adds callers, not fields.

## `DietaryRequirement` / `DietarySeverity` — existing, unchanged, `graph/models.py`

Reused as-is. `agents/dining/nodes/allergen_check.py` already produces
`DietaryConflict` records keyed by these; this feature changes *where the blocking case
routes to* (ADR-029), not the detection logic itself.

## Policy Engine internal types — new, `policy/`

Not `ResortFlowState` keys — internal to the `policy/` package, returned by its functions and
consumed by call sites, which translate the relevant parts into a `PolicyFlag` /
`SagaEvent` / guest-facing message.

**Jurisdiction scope (FR-019)**: `GamingClearance` (via `ResponsibleGamingStatus.
jurisdiction`) and `mandated_message_for`'s lookup are the only two jurisdiction-
parameterized paths in this feature. `DietaryFidelityResult`, `InjectionScreeningResult`,
and the redaction pass below are deliberately jurisdiction-invariant — allergen safety,
injection security, and PII categories aren't NGCB/MGC-specific regulations, so the same
rule applies at both tenant properties by design, not by omission.

```python
class GamingClearance(NamedTuple):
    """Result of require_gaming_clear(). Consumed by both call sites (gaming
    lookup, Offer Agent) so both branch on the same shape."""
    permitted: bool
    status: ResponsibleGamingStatus | None
    """None only when dependency_unavailable — see `failure_reason`."""
    failure_reason: Literal["self_excluded", "dependency_unavailable"] | None


class DietaryFidelityResult(NamedTuple):
    """Result of the post-flight allergen check — distinct from
    DietaryConflict (which is about menu-content matching, whose fields —
    menu_item/chunk_id/source_uri — don't fit here); this is about
    conversation-vs-structured-payload fidelity specifically. `missing` is
    `list[DietaryRequirement]` (the requirements themselves), not
    `list[DietaryConflict]`, per a refinement made during implementation —
    see the Phase 6 handoff."""
    fidelity_ok: bool
    missing: list  # list[DietaryRequirement]


class InjectionScreeningResult(NamedTuple):
    """Per-chunk verdict, produced before a retrieved chunk is added to a
    generation context."""
    chunk_id: str
    verdict: Literal["clean", "suspect"]
    matched_pattern_id: str | None
    similarity_score: float | None


class DistressCheck(NamedTuple):
    """Result of screening one guest utterance for gambling-related
    distress. `ambiguous=True` routes to handoff too (FR-018) — there is no
    third, "unsure, proceed normally" outcome."""
    flagged: bool
    ambiguous: bool
```

## Redaction contract — new, `observability/redaction.py`

Not a stored entity; a transform applied at the trace/log write boundary. See
`contracts/redaction-contract.md` for the full field-level allowlist. Summary: free text
and identifying fields are passed through Presidio; fields already typed as a closed
enum (`DietaryRequirement.restriction`, `DietaryRequirement.severity`, `PolicyFlag.rule`,
`PolicyFlag.disposition`) are exempted by an explicit allowlist, not by the NLP pass
happening to miss them.

## Jurisdiction message lookup — new, small static data

```python
JURISDICTION_MESSAGES: dict[str, dict[str, str]]
# {"NV": {"gaming_content": "...", "offer": "..."}, "MA": {...}}
```

Deterministic dict lookup by `(jurisdiction, trigger)`, `trigger ∈ {gaming_content,
offer}` per the spec clarification (FR-012). No entity, no schema — a small static
mapping module, not part of `ResortFlowState`.

## Node registry — no new node names

`graph/nodes.py`'s `POLICY_PREFLIGHT`, `POLICY_POSTFLIGHT`, `BLOCKED`, `OFFER_AGENT`,
`HUMAN_HANDOFF` were reserved for exactly this feature (module docstring, ADR-020) and
require no additions — the hard rule against renaming a node in a patch release is
honored by construction, since nothing is renamed and nothing new is added to the
registry. `graph/supervisor.py`'s `make_policy_preflight_node`/
`make_blocked_node` gain real bodies; `_route_from_policy_preflight` gains real logic in
place of its current `# no real rule logic yet (Phase 6) — always passes` stub.

## State transitions summary (for tasks.md test design)

- **Gaming lookup, any intent** → `require_gaming_clear` → clear: proceed to the
  existing per-intent branch; not clear (`self_excluded`): suppression message per the
  existing `_format_rg_status` path, `PolicyFlag(rule="self_exclusion_active",
  disposition="blocked")`; not clear (`dependency_unavailable`): "temporarily
  unavailable" message, `PolicyFlag(rule="dependency_unavailable", disposition="blocked")`
  + `escalated`, rest of conversation unaffected (FR-021).
- **`OFFER_AGENT`, every turn** → same `require_gaming_clear` gate before
  `get_loyalty_status` is ever called.
- **Dining allergen check, blocking severity** → routes to `BLOCKED` (not
  `HUMAN_APPROVAL`), `PolicyFlag(rule="allergen_fidelity_mismatch", disposition="blocked")`
  + `escalated`, `SagaEvent(kind="dietary_conflict_flagged")` (existing, unchanged) —
  clears only on a subsequent turn where the structured record no longer conflicts.
- **Retrieved chunk screening** → `InjectionScreeningResult.verdict == "suspect"` →
  excluded from generation context, `PolicyFlag(rule="injection_detected",
  disposition="blocked")`; `"clean"` → used normally, no flag (only exceptions are
  recorded, matching `failures`'/`saga_log`'s existing append-only-exceptions style).
- **Any turn, `DistressCheck.flagged or .ambiguous`** → route to `HUMAN_HANDOFF` (existing
  node, ADR-020), `PolicyFlag(rule="distress_signal", disposition="escalated")`.
- **Any response containing gaming content or an offer** → jurisdiction message appended
  deterministically, `PolicyFlag(rule=..., disposition="messaged")` recorded for audit
  (does not block).
