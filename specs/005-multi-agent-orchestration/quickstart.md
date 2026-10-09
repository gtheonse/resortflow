# Quickstart: Validating Multi-Agent Orchestration

`make demo` and `make eval` are currently stubs ("not yet implemented — lands once
dining/entertainment/stay domains exist" / "lands with the eval track"). This feature is
what makes both real for the first time. This guide is the runnable validation this
feature's exit criteria demand — it does not duplicate `data-model.md` or the
`contracts/` files, only references them.

## Prerequisites

- `make up` — brings up the sims (Track A), the MCP gateway (Track B, now including
  `entertainment`), and Postgres, per `docker-compose`.
- `make seed` — seeds both tenants (`MGV`, `MHB`), venues, events, rooms, and guest
  profiles, including at least one guest with a structured dietary restriction on file
  (exercises the dining-in-a-multi-domain-itinerary path, spec.md FR-019).
- `POSTGRES_URL` set, so `compile_dining_graph`/the supervisor's checkpointer and
  `PostgresHoldLedger` (not `NullHoldLedger`) are actually exercised — this quickstart is
  meaningless against the in-memory fallback, since its entire point is proving durable,
  multi-domain hold safety.

## Scenario 1 — Three-domain itinerary succeeds end-to-end (User Story 1, SC-001)

```bash
python -m graph.demo \
  --tenant MGV --guest <seeded-guest-id> \
  --text "Dinner, a show, and a spa treatment tomorrow evening"
```

**Expected**: the supervisor routes through `IntentRouter -> Supervisor -> PolicyPreflight
-> Planner -> FanOut{Dining,Entertainment,Wellness}`, all three domains report a
`Proposal`, `ConflictCheck` finds no conflict (or the demo script picks a scenario where
it doesn't), `HumanApproval` interrupts for guest confirmation, and — after approving —
`Commit` confirms entertainment, then dining, then wellness (commit order per
`docs/adr/` reference in `research.md`'s "Sequential commit ordering" decision), landing
at `Respond` with all three confirmations. Verify against `graph-topology-v005.md`'s
resolved topology diagram.

**Check**: query `hold_ledger` for the `saga_id` — all three rows should be `CONFIRMED`,
none `PENDING`/`HELD` after the run completes.

## Scenario 2 — Injected dining failure triggers recovery, not cancellation (User Story 2, SC-002)

```bash
python -m graph.demo \
  --tenant MGV --guest <seeded-guest-id> \
  --text "Dinner, a show, and a spa treatment tomorrow evening" \
  --inject-failure dining:confirm_reservation
```

**Expected**: entertainment and wellness commit successfully; dining's `confirm_reservation`
fails (simulator fault injection); `CommitVerify` detects the partial failure and routes
to `Recovery`; `Recovery` proposes an alternative dining slot/venue; the guest sees a
message naming what is already confirmed and the new dining alternative — never a full
itinerary cancellation.

**Check**: `saga_log` for this `saga_id` contains a `partial_failure` event followed by a
`compensated` (or equivalent alternative-proposed) event, never a `released` event for
the entertainment or wellness holds that already confirmed.

## Scenario 3 — Domain fully unavailable produces a partial itinerary (clarified FR-021)

```bash
python -m graph.demo \
  --tenant MHB --guest <seeded-guest-id> \
  --text "Dinner and a show tomorrow" \
  --force-unavailable entertainment
```

**Expected**: `Planner` (or a replan pass) produces a plan covering only dining;
`saga_log` records a `partial_itinerary_proposed` event (data-model.md); the guest's
response names entertainment specifically as unavailable rather than failing the whole
request.

## Scenario 4 — Adversarial input exhausts the replan bound and escalates (User Story 5, SC-004)

```bash
python -m graph.demo \
  --tenant MGV --guest <seeded-guest-id> \
  --text "Book me literally anything, nothing is ever available" \
  --force-unavailable dining entertainment stay wellness
```

**Expected**: `Planner -> FanOut -> ConflictCheck -> Planner` cycles exactly
`MAX_REPLAN_COUNT` (3) times, then `escalate=True`,
`escalate_reason="replan_bound"`, routing to `HUMAN_HANDOFF`. The conversation must not
attempt a fourth replan. A follow-up message to the same `session_id` must not silently
resume automated retrying (spec.md User Story 5, Acceptance Scenario 2).

## Scenario 5 — Ambiguous request triggers a clarifying turn (FR-020, ADR-024)

```bash
python -m graph.demo --tenant MGV --guest <seeded-guest-id> --text "Book me a nice evening"
```

**Expected**: `IntentRouter -> Clarify` (missing date/domain), guest is asked a specific
follow-up question, `clarification_count` increments to 1, guest answers, `Clarify ->
IntentRouter`, classification re-runs against the combined input. Confirm the case where
the guest never resolves the ambiguity after `MAX_CLARIFICATION_COUNT` (2) rounds also
reaches `HUMAN_HANDOFF` with `escalate_reason="clarification_bound"`.

## Scenario 6 — Concurrent fan-out safety under partial node failure (FR-007, SC-003)

This is the hardest property to demonstrate via a CLI script alone — it is primarily an
automated test, not a manual demo step:

```bash
pytest agents/tests/test_fanout_pending_write_safety.py -v
```

**Expected**: a test that starts a three-domain fan-out, forces one domain's node to
raise mid-superstep, resumes the thread, and asserts (a) the other two domains' holds
appear exactly once in `hold_ledger` (not zero, not duplicated), and (b) the failed
domain's node re-runs on resume without re-executing the domains that already wrote
successfully — directly exercising the guarantee `ResortFlow-HITL-Durability-State.md` §2
describes ("the fan-out superstep runs Dining, Entertainment and Wellness in parallel...
the successful nodes' writes are already durable and don't re-run on resume").

## Eval suite generalization

`agents/dining/eval/runner.py`'s `run_case`/`run_all`/`write_results` pattern generalizes
directly: `agents/entertainment/eval/`, `agents/stay/eval/`, `agents/wellness/eval/` each
get their own `test_cases.yaml` + `runner.py` following the exact same shape, plus one
new `agents/supervisor/eval/` (or equivalent) covering the six scenarios above as
multi-domain cases. Whether these get wrapped under a real top-level `eval/` harness is a
Track E decision (research.md's "Eval suite expansion is a Track E coordination point");
this feature's own `make eval` wiring, once Track E's shape is decided, should invoke all
of the above.

## `make demo` and `make eval` — real targets, replacing the stubs

```makefile
demo:
	python -m graph.demo --tenant MGV --guest $$(python -m sims.seed_all --print-demo-guest) \
	  --text "Dinner, a show, and a spa treatment tomorrow evening" \
	  --inject-failure dining:confirm_reservation

eval:
	python -m agents.dining.eval.runner && \
	python -m agents.entertainment.eval.runner && \
	python -m agents.stay.eval.runner && \
	python -m agents.wellness.eval.runner
```

(Final `eval` target shape is Track E's call; the above is this feature's minimum bar —
every domain this feature ships has a passing eval suite before the feature is
considered converged, per the constitution's "TEST BEFORE ADVANCE" standard.)
