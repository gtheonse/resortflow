# Contract: `policy/` public interface

The Policy Engine is a plain Python package (research.md, ADR-028) — this contract is
its call surface, so every node/subgraph that needs a policy decision depends on this
interface, never on ad hoc logic re-derived at the call site (which is exactly the
pattern this feature retires: `agents/dining/nodes/allergen_check.py` and
`agents/gaming/nodes/lookup.py` currently each implement their own inline version of
part of this).

## Functions

```python
# policy/gaming.py
async def require_gaming_clear(
    tools: GamingTools, *, guest_id: str,
) -> GamingClearance:
    """Calls tools.check_rg_status(guest_id=guest_id). Never caches across
    calls (FR-016) — every call is a fresh vendor check. On a tool-level
    failure (GamingToolError), returns
    GamingClearance(permitted=False, status=None,
    failure_reason="dependency_unavailable") rather than raising — the two
    call sites (gaming lookup, Offer Agent) both need a value, not an
    exception, since a dependency outage must fail closed for the affected
    capability only (FR-021), not crash the turn."""


# policy/dietary.py
def check_dietary_fidelity(
    proposal: Proposal, conversation_restrictions: list[DietaryRequirement],
) -> DietaryFidelityResult:
    """Post-flight check (FR-006): every restriction mentioned in
    conversation_restrictions must appear in proposal.dietary. This is
    fidelity (conversation vs. structured payload), distinct from
    allergen_check's existing menu-content matching (DietaryConflict) —
    both must pass before confirm."""


# policy/injection.py
def screen_retrieved_content(
    chunks: list[Chunk], *, corpus: InjectionCorpus,
) -> list[InjectionScreeningResult]:
    """Pattern + embedding-similarity screen (ADR-032). Pure function of
    its inputs — no network call, no LLM. One result per input chunk,
    same order."""


# policy/distress.py
def check_distress_signal(utterance: str) -> DistressCheck:
    """Deterministic phrase/embedding match (research.md). No model
    judgment (constitution, FR-004) — this function's only backend is the
    same pattern/embedding technique as screen_retrieved_content."""


# policy/jurisdiction.py
def mandated_message_for(jurisdiction: str, trigger: Literal["gaming_content", "offer"]) -> str | None:
    """Deterministic dict lookup (FR-012). Returns None if no message is
    configured for that (jurisdiction, trigger) pair — callers append
    nothing in that case, they never synthesize a substitute."""
```

## Call sites (who calls what)

| Call site | Function(s) called | On not-clear/failure |
|---|---|---|
| `agents/gaming/nodes/lookup.py` (all three intents) | `require_gaming_clear` | Suppression message; `PolicyFlag` per data-model.md |
| `graph/supervisor.py`'s new `OFFER_AGENT` node | `require_gaming_clear`, then `mandated_message_for` on any offer produced | Skip to a no-offer response, no `get_loyalty_status` call made |
| `agents/dining/nodes/allergen_check.py` | `check_dietary_fidelity` (new call, alongside its existing `DietaryConflict` menu-matching logic, unchanged) | Route to `BLOCKED`, never `HUMAN_APPROVAL` (ADR-029) |
| Each domain's retrieval call site (`agents/dining/menu.py` and analogues) | `screen_retrieved_content` on the chunks about to enter a generation context | Suspect chunks excluded from context before it's built |
| `graph/supervisor.py`'s `make_policy_preflight_node` | `check_distress_signal` on the latest guest utterance | Route to `HUMAN_HANDOFF` |
| Any node about to emit a gaming-content or offer-bearing guest message | `mandated_message_for` | N/A — always deterministic append-or-nothing |

## What this contract does not cover

- PII redaction — its own contract, `redaction-contract.md` (a different boundary: trace
  writes, not guest-facing decisions).
- `PolicyFlag` shape and the `policy_flags` reducer amendment —
  `state-schema-amendment-006.md`.
- Graph wiring (which node calls which of the above, and the new edges) —
  `graph-topology-v006.md`.
