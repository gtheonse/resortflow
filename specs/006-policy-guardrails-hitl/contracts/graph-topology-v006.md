# Graph topology amendment 006

Companion to `specs/005-multi-agent-orchestration/contracts/graph-topology-v005.md`.
No node is renamed or removed (hard rule). Every node this feature gives real behavior
to was already reserved in `graph/nodes.py` since feature 004/005 — this amendment
documents behavior, not new names.

## Nodes gaining real behavior (were no-op stubs)

| Node | Before (feature 005) | After (this feature) |
|---|---|---|
| `POLICY_PREFLIGHT` (`make_policy_preflight_node`) | Pass-through, returns `{}` | Calls `check_distress_signal` on the latest guest utterance; routes to `HUMAN_HANDOFF` on `flagged or ambiguous`. Entitlement/jurisdiction/self-exclusion gating for gaming stays local to the gaming subgraph and `OFFER_AGENT` (below), not duplicated here — `POLICY_PREFLIGHT` sits before domain routing and does not yet know which domains a multi-domain request will touch. |
| `_route_from_policy_preflight` | Always returns `PLANNER` (`# no real rule logic yet (Phase 6)`) | Returns `BLOCKED` (via `HUMAN_HANDOFF`, see above) only on a distress signal; otherwise unchanged — `PLANNER`. |
| `BLOCKED` (`make_blocked_node`) | Unreachable (no edge routes to it yet) | Reachable from dining's allergen-fidelity block and from gaming's self-exclusion/dependency-unavailable block (ADR-029, ADR-030) with a policy-specific message in place of today's generic "This request cannot be completed as policy stands," and from `POLICY_POSTFLIGHT`'s injection-detected path where relevant. |
| `POLICY_POSTFLIGHT` | No-op (implicit; not yet given a `make_*_node` function in feature 005) | New `make_policy_postflight_node`: applies `mandated_message_for` to any gaming-content/offer-bearing response about to reach the guest (FR-012). Allergen fidelity and self-exclusion are checked at their own domain-local points (dining allergen_check, gaming lookup/Offer Agent), not re-checked here — this node owns only the cross-cutting message-appending step, which is genuinely cross-domain. |
| `OFFER_AGENT` | Reserved, uncompiled (ADR-020) | Compiled this feature: `require_gaming_clear` → (if clear) `GamingTools.get_loyalty_status` → `mandated_message_for` on any offer surfaced → guest-facing message. Mounted in the supervisor graph per the cross-domain path; single tool call, no hold/approve/confirm lifecycle (matches gaming's own read-only shape — no ledger parameter). |
| `HUMAN_HANDOFF` | Real since feature 005 (ADR-020), reached from `RECOVERY` and bounded-cycle breaches | Additional callers this feature: `POLICY_PREFLIGHT` (distress signal), dining/gaming policy-block escalation paths where `disposition="escalated"` is recorded. No change to `HUMAN_HANDOFF`'s own node body. |

## Edges amended, per domain subgraph

**Dining** (`agents/dining/edges/conditions.py`): `route_after_hold_placement` currently
sends any `awaiting_approval` case to `HUMAN_APPROVAL`. This feature splits that: a
blocking dietary-fidelity result (new `check_dietary_fidelity`, alongside the existing
`DietaryConflict` check) routes to `BLOCKED`; an ordinary booking needing approval (no
policy block) continues to `HUMAN_APPROVAL` exactly as today. `human_approval`'s own body
is unchanged — it stops being reachable *for the blocking case specifically*, not
rewritten.

**Gaming** (`agents/gaming/graph.py`, `agents/gaming/nodes/lookup.py`): `lookup`'s three
intent branches are now preceded by one `require_gaming_clear` call, replacing the
current `rg_status`-intent-only check. `ALL_NODE_NAMES` (`INTENT_PARSER`, `LOOKUP`) is
unchanged — this is a body change inside the existing `lookup` node, not a new node.

**Cross-domain** (`graph/supervisor.py`): `make_human_approval_node`'s approve/decline/
modify contract is unchanged (research.md, ADR-029) — a domain that hit a policy block
is simply absent from `approval_scope`'s held-proposals set by the time this node runs,
the same way a domain with no availability is already excluded per feature 005's partial-
itinerary handling (FR-021 precedent). No new `Command.goto` target is added to this
node.

## Sequence: self-excluded guest asks about floor games (closes the concrete gap)

```
Guest: "what's on the casino floor tonight?"
  -> INTENT_ROUTER -> GAMING subgraph
  -> INTENT_PARSER: intent_kind="floor_games"
  -> LOOKUP: require_gaming_clear(guest_id)   # NEW — runs before every intent, not just rg_status
       -> check_rg_status: self_excluded=True, gaming_content_permitted=False
     -> suppression message (existing _format_rg_status path), list_casino_floor NEVER called
     -> PolicyFlag(rule="self_exclusion_active", disposition="blocked")
```

Today, this same guest utterance calls `list_casino_floor` directly and returns real
floor content — this sequence is the exit-criteria-relevant fix.
