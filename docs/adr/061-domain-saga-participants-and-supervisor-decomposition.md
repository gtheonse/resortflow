# ADR-061 — Domain saga participants and decomposition of the supervisor

**Status**: Proposed (2026-10-02)
**Date**: 2026-10-02
**Feature**: supervisor refactor (branch `refactor/supervisor-decomposition`)
**Amends**: ADR-029 (policy-block routing — one edge, see Decision §4)
**Guide**: [ResortFlow-Supervisor-Architecture.md](../ResortFlow-Supervisor-Architecture.md) (read this first if the terms are new)

## In one paragraph

`graph/supervisor.py` had grown to 2,664 lines and contained everything the multi-domain booking flow
needed, including every domain's private details. We split it into eleven single-purpose modules and
moved each domain's details into that domain's own package behind two small interfaces. Behaviour is
preserved (node names, state shape and checkpoint identity are unchanged and test-pinned), with one
deliberate correction: the standalone dining path now enforces the same allergen guarantee as the
multi-domain path.

## Context

### What was wrong

An external architecture review called the file a "God Object". We checked every claim against the code
rather than adopting the review wholesale. These findings held up:

1. **One module, about ten reasons to change.** Graph wiring, intent routing, read-only answers, standalone
   dispatch, planning and fan-out, conflict checking, approval, commit/verify/recovery/release, policy gates
   and reply composition all lived together.
2. **Domain knowledge was scattered across nine per-domain tables or branches** inside the supervisor:
   tool factories, fan-out runners, standalone compilers, fan-out domain list, release-verb lookup,
   confirm-node factory, independent verifiers, alternative searchers, and commit order. Adding a sixth
   domain meant editing nine places. This violates Open/Closed and Dependency Inversion.
3. **The four propose-and-hold runners repeated one skeleton** (accumulate state, try, convert the domain's
   error to a `ToolFailure`).
4. **Policy was implemented twice, and the copies disagreed.** This is the most important finding and it
   is not about file size. The multi-domain runner re-implemented the allergen check inline (importing the
   dining package's *private* `_BLOCKING_SEVERITIES` and `_extract_dietary`). It blocked **before** any hold
   was placed. The standalone dining graph placed the hold **first**, then blocked, and left the hold to
   the TTL reaper. A guest who could never confirm still tied up vendor inventory, and the same sentence
   got a different guarantee depending on whether it also mentioned a show. This contradicts commitment 4
   ("policy is not a prompt") and the intent of commitment 3.
5. **Engine plumbing sat inside orchestration.** `_deterministic_core` handled sub-thread identity,
   interrupt forwarding, message diffing and append-only deduplication.

### What the constraints are

- Node names and the sub-thread id are **persistence contracts**; renaming breaks resume of existing
  conversations (AGENTS.md hard rule).
- `ResortFlowState` and the MCP/OpenAPI tool vocabulary are **frozen** (Phase 0).
- 55 files import from `graph.supervisor`, including tests that read function source by name.
- Gaming is **read-only**; it must never be able to place a wager or transaction.

## Options Considered

### A. Leave it; add comments and section banners
Cheapest. Fixes nothing structural, and finding 4 (divergent policy) is a correctness defect that
documentation does not repair. Rejected.

### B. Adopt the external review as written
The review proposed domain adapters in `graph/adapters/`, a `graph/nodes/` package, a single wide
`DomainAgentAdapter` protocol, `prepare()` returning `(proposals, holds, dict)`, dataclass state
projections for every node, mounted subgraphs, a hexagonal rewrite of all simulators, a split of the
eval runner, and a new policy interceptor chain. Parts are right; parts would do harm:

| Review proposal | Disposition | Reason |
|---|---|---|
| Replace per-domain tables with a domain abstraction | **Accepted**, reshaped | Fixes Open/Closed and Dependency Inversion. |
| Put adapters in `graph/adapters/` | **Rejected** | The orchestrator would still import domain internals; same coupling, new folder. Abstraction belongs in `graph/`, implementations in `agents/<domain>/`. |
| One wide `DomainAgentAdapter` (tools + prepare + commit + verify + alternatives + compile) | **Rejected** | Violates Interface Segregation, and would let read-only gaming "implement" a commit. Split in two. |
| `prepare()` returns a tuple | **Rejected** | Drops `policy_flags`, `saga_log`, `messages`, `failures`. Participants return a `ResortFlowState` delta so reducers keep working. |
| "Liskov violation: `release_seat_hold` vs `release_hold`" | **Rejected as stated** | Those are the vendors' real verbs, frozen in the contracts. Renaming is a contract change. The fix is an anti-corruption adapter per domain (a DIP/OCP fix, not LSP). |
| New `graph/nodes/` package | **Rejected** | Collides with `graph/nodes.py`, the frozen node-name registry. |
| Separate `routes/` folder | **Rejected** | A conditional edge is part of its node's contract; keep it beside the node. |
| Typed state projections for every node | **Rejected** | `ResortFlowState` is frozen and ADR-governed; per-node dataclasses are ceremony. Narrowing is applied only where it earns its keep, at the participant boundary (`ConfirmContext`). |
| Mount domain graphs as real subgraphs | **Deferred** | Changes the sub-thread checkpoint identity: needs a `GRAPH_VERSION` major bump and its own ADR. We isolate the plumbing instead. |
| Hexagonal rewrite of `sims/*` | **Rejected** | Sims already share an app factory, fault injection, holds, idempotency, tenancy and a webhook harness. They are cohesive per vendor, fault-injectable test doubles, with contracts frozen by Track A. The "60% duplicated" claim is false. |
| Split `eval/runner.py` into per-tier files | **Rejected** | The three tier functions are short linear sequences; nothing to split. |
| Policy interceptor chain | **Rejected** | Policy already has graph-level gates and a `policy/` package; a parallel pipeline would recreate the divergence this ADR removes. |
| "Adding a domain needs zero supervisor edits"; "~150-line supervisor" | **Rejected as overstated** | Fan-out branches are named graph nodes (a persistence contract), so a new domain still adds one node constant and one registration. The honest claim is "no logic edits". |

### C. Participant abstraction plus package decomposition (chosen)

## Decision

### 1. Two narrow interfaces, owned by `graph/`

[graph/participants.py](../../graph/participants.py):

- **`StandaloneDomain`** — `domain`, `tools()`, `compile()`. Implemented by all five domains.
- **`SagaParticipant`** extends it with `commit_rank`, `propose_and_hold`, `confirm`, `release`,
  `find_alternative`, `verify`. Implemented by the four mutating domains. **Gaming does not and cannot.**
- **`ConfirmContext`** — the frozen slice (`proposal`, `hold`, `guest_id`, `saga_id`) the confirm step needs.
- **`DomainRegistry`** — `standalone()`, `participant()`, `fanout_domains()` (registration order),
  `commit_order()` (by `commit_rank`), `tools_factory()` (the existing test-override seam).

`verify` returns `bool | None`; **`None` means "no independent vendor check exists"** and is never
treated as a pass. This keeps the disclosed verification gaps for dining, wellness and stay visible.

### 2. Domain-owned adapters; one composition root

Each `agents/<domain>/participant.py` owns that domain's pipeline, vendor release verb and recovery
search. [graph/domains.py](../../graph/domains.py) is the only module that names concrete domains.
`agents/_shared/pipeline.py` holds the single propose-and-hold skeleton that four runners used to repeat.

### 3. The supervisor becomes a package, behind a facade

`graph/supervisor/` has eleven modules (largest 362 lines): `common`, `session`, `intent`, `read_paths`,
`deterministic`, `subgraph_bridge`, `policy_gates`, `planning`, `saga`, `respond`, `topology`. The
module dependency graph is acyclic (checked mechanically when split). `__init__.py` re-exports every
name the old module exposed, so none of the 55 importers changed.

### 4. One allergen decision (amends ADR-029)

`evaluate_allergens()` in `agents/dining/nodes/allergen_check.py` is the single policy decision. Both
the standalone node and `DiningParticipant` call it. In the standalone dining graph,
`ALLERGEN_CHECK` gains a conditional edge, `route_after_allergen_check`: blocked goes to `END` before
`hold_placement`. The `policy_blocked` guard in `route_after_hold_placement` is **kept** as defence in depth.

ADR-029's text said the placed hold "is left to the existing hold-ledger reaper (ADR-022)". That sentence
is superseded: no hold is placed on a block.

### 5. Sub-thread plumbing isolated

`graph/supervisor/subgraph_bridge.py` takes the sub-thread id, interrupt forwarding and append-only
deduplication verbatim. No behaviour change.

### 6. Invariants preserved

Node-name set and edge set of the supervisor graph (golden-tested), `ResortFlowState` (25 keys),
`GRAPH_VERSION` 5.2, sub-thread id format, saga commit order, fan-out insertion order, `tenant_id` sourcing.

### 7. Eval coverage rule updated

`eval/coverage.py` identified "saga surface" by the exact path `graph/supervisor.py`. After the split that
check would silently stop firing, so it now matches the `graph/supervisor/` package, `graph/participants.py`,
`graph/domains.py` and `agents/*/participant.py`.

## Consequences

**Good**
- Adding a domain touches no saga or planning logic: one participant, one registration, one node constant.
- Policy has one implementation and one behaviour on every path; a parity test proves it.
- The read-only status of gaming is structural, not conventional.
- A reviewer can read one small file per concern (the largest is 362 lines).

**Costs and honest limits**
- A new domain still adds one node constant and one registration (node names are persistence contracts).
- `propose_and_hold` still receives the full `ResortFlowState` because the existing domain node functions read
  state dictionaries; narrowing it would mean rewriting every domain node.
- LangGraph types (`interrupt`, `AIMessage`, reducers) remain in the orchestration layer. The graph
  runtime *is* the application framework here; wrapping it would be ceremony with no second implementation.
- Wellness and stay now construct their tool object during verification even though their `verify`
  returns `None` (previously skipped). The effect is cosmetic.
- Standalone dining behaviour changed on a block: no hold is placed. Two test assertions that expected a
  held reservation were updated accordingly.

**Verification**: `agents`, `graph` and `policy` suites — 632 passed, 2 skipped, 1 failed. The one failure
(`test_tenant_isolation.py::test_no_client_supplied_field_can_override_the_verified_tenant`) fails identically
on the untouched original commit (the gateway correctly rejects the injected `tenant_id`, so the test's mocked upstream is never called). The live eval gate (`--tier pr`) was run on both the
original and refactored trees: the same six single cases fail on both (the verdict was already FAIL), and 439 of 440 metric rows are
identical, the exception being one LLM-judged `answer_relevance` score.

**Foreclosed**: a new domain's recovery, release or verification may not be added as an `if domain is ...`
branch in the supervisor; it belongs in that domain's participant.
