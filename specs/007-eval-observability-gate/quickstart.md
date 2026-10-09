# Quickstart: Validating the Evaluation & Observability Gate

Prerequisites: local stack up (`make up`), tenants/content seeded (`make seed`).
No live cloud credentials are required for any scenario below except where noted
— that absence is itself the thing being validated (FR-013/SC-006).

## 1. PR tier runs and passes on a clean branch

```bash
python -m eval.runner --tier pr
```

Expected: exits 0, `eval/data/reports/<run-id>.json` shows `verdict: PASS`,
`judge_model.model_id` is a pinned (non-`latest`) identifier, and it completes in
under ~10 minutes (SC-010). Cross-check against
[eval-report-schema.md](contracts/eval-report-schema.md).

## 2. A deliberately degraded prompt fails the gate (SC-002, spec User Story 1)

1. Edit a domain prompt to remove grounding instructions (any change that would
   make an answer less faithful to retrieved context).
2. Re-run `python -m eval.runner --tier pr`.
3. Expected: exits 1, `verdict: FAIL`, and the report's `metric_scores` names the
   regressed `faithfulness` (or `answer_relevance`) metric together with the
   judge model that produced it (FR-009).

## 3. A saga permutation proves recovery, not just the success path (User Story 2)

```bash
python -m eval.runner --tier merge_queue --only saga
```

Expected: `saga_case_results` lists every defined case across dining/stay/
entertainment individually (SC-003) — inspect
[data-model.md](data-model.md#saga-failure-case) for the case shape. Confirm at
least one `failing_step != none` case and one `failing_step == none` (success
path) case both appear and both pass.

## 4. Diagnosing one conversation end-to-end (User Story 3)

```bash
python -m eval.cli show-conversation --id <conversation_id>
```

Expected: every turn's tool calls, retrieved context, output, cost, and latency
print in chronological order, sourced from the same Conversation Record the
eval suite scores against (FR-012) — not a separate log.

## 5. Cost/latency budget flagging (User Story 4)

Run a conversation whose fixture deliberately exceeds a step's budget (see
`eval/data/` fixtures), then re-run the PR tier. Expected: `budget_flags` names
that step specifically; a conversation with no exceedance produces an empty
`budget_flags` list.

## 6. A required dependency outage produces INCOMPLETE, never PASS (FR-021–024)

```bash
EVAL_SIMULATE_JUDGE_OUTAGE=true python -m eval.runner --tier pr
```

Expected: exits non-zero locally is NOT required (local defaults to
`EVAL_INCOMPLETE_BLOCKS=false` per
[ci-tier-triggers-contract.md](contracts/ci-tier-triggers-contract.md)), but the
report MUST show `verdict: INCOMPLETE` and list the affected case under
`not_evaluated_cases` — never `PASS`. Re-run with
`EVAL_INCOMPLETE_BLOCKS=true` to confirm it exits 1, matching merge-queue
behavior.

## 7. Injected fault is not mistaken for an outage (FR-024)

Run the existing `sims` fault-injection test against a saga case using
`X-Sim-Fault: hold_expired` and confirm the eval report shows that case's
*actual* pass/fail result (the injected fault is the scenario under test), not
`NOT_EVALUATED`.

## 8. Coverage gate fails closed on an uncovered capability (User Story from
   FR-009a)

1. Add a new tool with `entitlement_action="commit"` and no corresponding saga
   case.
2. Run the PR tier. Expected: `verdict: FAIL`, `coverage` lists the new
   `surface_id` with `covered: false`.
3. Add an entry to `eval/data/exemptions.yaml` per
   [coverage-exemption-schema.md](contracts/coverage-exemption-schema.md).
   Re-run: `verdict` no longer fails on that surface; `active_exemptions` lists
   it.

## 9. An exemption expires and fails closed (FR-027, SC-013)

Set an exemption's `expires_at` in the past (without changing `status`) and
re-run. Expected: the gate treats it as `void` regardless of the stored
`status` value, and fails closed on that surface again.

## 10. Baseline invalidated by a judge-model change never reports a false
    regression (FR-007c, SC-015)

```bash
MODEL_JUDGE=<a-different-pinned-deployment> python -m eval.runner --tier merge_queue
```

Expected: `baseline_status: BASELINE_INVALID` in the report; the affected
statistical metrics are not reported as regressions; the absolute-floor check
still applies and can still fail the gate independently.

## 11. Zero dependency on a proprietary observability/evaluation platform (FR-013, SC-006)

Run scenario 1 with egress to any cloud vendor's *observability or evaluation*
service blocked (a mock/local OTLP collector standing in for the tracing
backend), while still allowing the judge-model LLM call itself (FR-013a — the
judge call is expected and unavoidable, not part of this scenario's
"no dependency" claim). The same `verdict` MUST be produced as when run with
tracing pointed at Langfuse, given identical inputs — only optional platform
add-ons (FR-016), which are not exercised in this repo yet, would be absent.

## 12. Switching the judge's LLM provider is a config change, not a code change (FR-013a, SC-017)

```bash
LLM_PROVIDER=<a-different-openai-compatible-provider> python -m eval.runner --tier pr
```

Expected: `eval/judge.py` and every scorer that calls it run unmodified —
only environment variables change (provider, endpoint, credentials, pinned
deployment name). This is deliberately not exercised against a real second
provider in this build (spec.md Assumptions — Azure OpenAI is the only
provider actually configured); the check here is that no source file needs
editing to point at one, not that a second provider is live in CI.
