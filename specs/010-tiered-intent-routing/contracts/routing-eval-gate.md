# Interface Contract: Routing evaluation gate

**Feature**: `010-tiered-intent-routing` | Extends the 007 gate (`eval/gate.py`) through its
existing statistical-metric path. No gate-combinator change.

## Data: `eval/data/routing_cases.yaml`

Schema: data-model `RoutingEvalCase`. Coverage enforced by `eval/tests/test_routing_cases_data.py`:

- ≥ 8 cases per `category` per tenant (11 categories, so ≥ 176 cases).
- Every example quoted in spec.md appears verbatim with its expected label.
- Both tenants carry `out_of_scope` and `lookup` cases (per-tenant reporting, US5 AS1).
- Every case has `split: dev | holdout`, about 70/30, stratified by category and tenant. Cases
  are authored **before** the vocabulary and independently of it (data-model.md).

**Blocking metrics are computed on `holdout` only.** `dev` scores are reported for diagnosis.
This stops the gate from measuring the vocabulary's agreement with cases written to fit it.

Knowledge floor data: `rag/eval/negative_set.yaml` gains `offtopic:` cases (≥ 10 per tenant). The
file is owned by Track D, so this is a cross-track addition in the integration commit.

## Metrics (`eval/routing_scorer.py`, tenant-stratified `MetricScore`, `gate_type="statistical"`)

| Metric | Definition | Threshold | Direction | Blocking |
|---|---|---|---|---|
| `routing_accuracy` | kind and service set both match | ≥ 0.95 (SC-001, calibrate R-15) | higher | yes |
| `oos_passthrough_rate` | `out_of_scope` cases decided `faq` or `book` | = 0.0 (SC-002) | lower | yes |
| `substring_trap_misroutes` | `substring_trap` cases routed to a contained-fragment service | = 0 (SC-003) | lower | yes |
| `modify_book_starts` | `modify` cases decided `book` | = 0 (SC-009) | lower | yes |
| `followup_accuracy` | `followup` cases correct without clarify | ≥ 0.95 (SC-013) | higher | yes |
| `llm_tier_share` | cases decided at tier 2 | ≤ 0.20 (SC-005) | lower | report-only until R-15, then yes |
| `knowledge_floor_refusal` | off-topic cases refused / answerable kept | 1.0 / 1.0 (SC-008) | higher | yes (`rag/eval` runner) |

## Credential handling

Without Azure credentials, `requires_tier2` cases go to `run.not_evaluated_cases` as
`routing_tier2_cases` (INCOMPLETE). Deterministic cases always score. Never a fabricated pass.

## Degraded check (constitution CI gate)

`eval/tests/test_routing_gate_degraded.py`: run the scorer with a vocabulary whose tolerance is set
to exact-only. `routing_accuracy` must fall below the floor and the verdict must be FAIL (US5 AS2).
