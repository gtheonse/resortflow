# ResortFlow — End-to-End Tracing & Observability
## Local Langfuse ($0) → Production Microsoft Foundry + Azure Monitor

*Verified against Microsoft Foundry Observability (GA March 2026) and Langfuse v4 ingestion, September 2026.*

---

## Table of Contents
1. [The Strategy and Why It Holds](#1-the-strategy-and-why-it-holds)
2. [The Identifier Model](#2-the-identifier-model)
3. [The Telemetry Abstraction Layer](#3-the-telemetry-abstraction-layer)
4. [The Span Tree](#4-the-span-tree)
5. [Propagation Across the Hard Boundaries](#5-propagation-across-the-hard-boundaries)
6. [Environment A — Local (Langfuse)](#6-environment-a--local-langfuse)
7. [Environment B — Production (Foundry + Azure Monitor)](#7-environment-b--production-foundry--azure-monitor)
8. [**The Twelve Complications**](#8-the-twelve-complications)
9. [Evaluation Strategy — Portable vs Platform](#9-evaluation-strategy--portable-vs-platform)
10. [PII Redaction](#10-pii-redaction)
11. [Sampling](#11-sampling)
12. [A Debugging Walkthrough](#12-a-debugging-walkthrough)
13. [Cost Model](#13-cost-model)
14. [Design FAQ](#14-design-faq)

---

## 1. The Strategy and Why It Holds

**Local development:** Langfuse in Docker. Free, unmetered, runs on a laptop, iterates in seconds.
**Production:** Microsoft Foundry Observability with Azure Monitor / Application Insights as the telemetry backbone.

This works because **both consume the same thing**: OpenTelemetry spans following the GenAI semantic conventions. Foundry's observability stack is built on OTel with GenAI semconv stored in Application Insights, and its trace-evaluation path is the recommended approach for agents *not* built on the Foundry Agent Service — explicitly including LangChain and custom frameworks — provided they emit GenAI-conventional spans to Application Insights.

So ResortFlow's LangGraph orchestrator is a first-class citizen in Foundry without changing a line of instrumentation.

### Why not just use one everywhere

**Why not Langfuse in production?** Guest conversation data would leave the Azure boundary into a third-party store. For a Gulf government client or an Indian NBFC under DPDP, that's a six-week security review you don't need to have. Self-hosting Langfuse on Azure solves residency but costs $150–270/mo for an always-on stack that cannot scale to zero — more than the application it observes. And you'd forfeit Foundry's built-in safety evaluators and adaptive red teaming.

**Why not Foundry locally?** It requires live Azure resources. You will run the eval suite dozens of times a day during the build. Langfuse runs on the laptop for free with no unit meter and no network round-trip.

### The division of labour in production

| Layer | Answers |
|---|---|
| **Azure Monitor / App Insights** | Is it up? Is it fast? What broke? *(SRE view)* |
| **Foundry Observability** | Is it correct? Is quality drifting? Is it safe? *(AI engineer view)* |
| **Cosmos audit store** | What did we decide, and can we prove it? *(compliance view)* |

Foundry's Agent Monitoring Dashboard brings token usage, latency, run success rate and evaluator scores into one view, which is what makes quality drift visible alongside runtime signals.

**The line to use:** *"Azure Monitor tells me the system is healthy. Foundry tells me it's correct. A 200 OK in 800ms containing a hallucinated venue recommendation is green on one and a P1 on the other."*

---

## 2. The Identifier Model

| ID | Scope | Lifetime | Maps to |
|---|---|---|---|
| `session_id` | One guest conversation | Minutes–days | LangGraph `thread_id`; Langfuse `sessionId`; custom dimension in App Insights |
| `trace_id` | One turn | Seconds | W3C 128-bit → App Insights `operation_Id` |
| `span_id` | One operation | Milliseconds | W3C 64-bit → App Insights `id` |
| `saga_id` | One multi-system transaction | **Spans multiple turns** | ResortFlow-generated span attribute |
| `idempotency_key` | One mutation attempt | Single call | `(session_id, hold_id, attempt)` |

### The subtlety to raise unprompted

`saga_id` deliberately outlives `trace_id`. A guest holds a table in turn 3, approves in turn 5, compensation runs in turn 6 — three traces, one transaction. Parent-child cannot express that. Two mechanisms:

1. `saga_id` as a queryable attribute on every span in every participating trace.
2. **OTel span links** from the commit span back to the hold span — causal relationship, machine-readable.

Parent-child is not the only relationship: span links exist for causally-related-but-not-parented work, and async messaging and cross-trace sagas are the two canonical uses.

### Cardinality discipline

- `session_id`, `guest_id`, `saga_id` → **span attributes only**. Unbounded.
- `tenant_id`, `node`, `domain`, `model`, `outcome` → safe as **metric dimensions**. Bounded.

Putting `guest_id` on a metric is how you get a five-figure Log Analytics bill.

---

## 3. The Telemetry Abstraction Layer

**This is the single most important mitigation in the whole design.** Everything in Section 8 traces back to having built this — or not.

One module, `observability/telemetry.py`, is the *only* place in the codebase where an attribute name appears. It does five things:

1. **Emits both attribute namespaces** on every span — `gen_ai.*` for Foundry, `langfuse.*` for Langfuse.
2. **Externalizes oversized payloads** to Blob Storage, leaving a URI in the span.
3. **Applies PII masking** before any attribute is set.
4. **Selects the exporter** from an environment variable.
5. **Validates span shape** so a malformed span fails loudly in CI rather than silently in production.

```python
# observability/telemetry.py

LARGE_PAYLOAD_THRESHOLD = 4096   # chars; below App Insights dimension limits

def start_agent_span(name: str, *, node: str, domain: str | None = None):
    """Single entry point. Never set attributes directly elsewhere."""
    span = tracer.start_span(name)
    # --- Foundry / GenAI semantic conventions ---
    span.set_attribute("gen_ai.operation.name", node)
    span.set_attribute("gen_ai.system", "az.ai.openai")
    # --- Langfuse namespace ---
    span.set_attribute("langfuse.observation.type", _lf_type(node))
    span.set_attribute("langfuse.trace.metadata.node", node)
    # --- ResortFlow namespace (both backends, queryable) ---
    span.set_attribute("resortflow.node", node)
    if domain:
        span.set_attribute("resortflow.domain", domain)
    return span


def set_payload(span, kind: str, value: str):
    """kind: 'input' | 'output' | 'context'. Handles size and masking."""
    value = mask_pii(value)
    if len(value) > LARGE_PAYLOAD_THRESHOLD:
        uri = blob.put(f"payloads/{span_id(span)}/{kind}.json", value)
        span.set_attribute(f"resortflow.{kind}.uri", uri)
        span.set_attribute(f"resortflow.{kind}.truncated", True)
        value = value[:LARGE_PAYLOAD_THRESHOLD]
    # dual namespace
    span.set_attribute(f"gen_ai.{'prompt' if kind=='input' else 'completion'}", value)
    span.set_attribute(f"langfuse.observation.{kind}", value)
```

**Cost of dual-namespace emission:** roughly 6–10 extra attributes per span. Negligible in bytes, and it buys total backend portability. This is the concrete payoff of "instrument once, export anywhere"; the whole mapping lives in one module.

---

## 4. The Span Tree

One guest message — *"Book us dinner before tonight's show"*:

```
[ROOT] resortflow.turn                          gen_ai.operation.name=agent
│  session.id / langfuse.session.id / resortflow.session_id = sess_9f3a
│  resortflow.tenant_id=MGV   resortflow.saga_id=saga_44c   resortflow.graph_version=v1.4.2
│
├── resortflow.router                           type=chain
│   └── gen_ai.chat                       gen_ai.request.model=gpt-4.1-mini
│                                         gen_ai.usage.input_tokens=412
├── resortflow.policy.preflight                 type=guardrail
│                                         rg_checked=true  self_excluded=false
├── resortflow.retrieval                        type=retriever
│   ├── qdrant.hybrid_search              top_k=30  tenant_filter=MGV
│   └── rerank.cross_encoder              kept=5
│                                         resortflow.context.uri=blob://...   ← externalized
├── resortflow.planner                          type=chain  → gen_ai.chat (gpt-4.1)
├── resortflow.fanout
│   ├── resortflow.agent.dining        [parallel]  → gen_ai.chat + mcp.dining.create_hold
│   └── resortflow.agent.entertainment [parallel]  → gen_ai.chat + mcp.ticketing.hold_seats
├── resortflow.conflict_check                   type=span, NO LLM
├── resortflow.interrupt.approval               type=event  → graph suspends, checkpoint written
│   ══ turn ends; new trace on resume, linked by saga_id + span links ══
├── resortflow.commit.entertainment             type=tool   link→ hold span (prior trace)
├── resortflow.commit.dining                    type=tool   status=ERROR  HOLD_EXPIRED
├── resortflow.recovery                         type=agent  strategy=reschedule
└── resortflow.policy.postflight                type=guardrail  groundedness=0.94
```

Note `resortflow.context.uri` on the retrieval span — the full reranked context lives in Blob, not in a custom dimension. That's Complication #4 pre-solved.

---

## 5. Propagation Across the Hard Boundaries

| # | Boundary | Mechanism |
|---|---|---|
| 1 | Client → APIM | APIM policy generates/accepts `traceparent`; extracts `tenant_id` from validated JWT into a header |
| 2 | APIM → Orchestrator | W3C extraction + **baggage** for trace-level attributes |
| 3 | In-process LangGraph | Langfuse `CallbackHandler` locally / OTel auto-instrumentation; `thread_id == session_id` |
| 4 | Node → MCP tool | `inject()` traceparent into outbound HTTP |
| 5 | MCP → vendor sim | Sims echo traceparent (demo only — real vendors are a black box) |
| 6 | Service Bus → Function | **Span links**, not parent-child, per OTel messaging conventions |
| 7 | Vendor webhook | **Correlation store** (Redis: `order_id → trace_id, span_id, saga_id`), linked span on receipt |

### The baggage requirement (both backends need it)

Langfuse now filters across individual observations, not only at trace level, so `sessionId`, `userId`, metadata and tags must be present on **every span** — set them only on the root and session filtering silently returns partial results. Use a `BaggageSpanProcessor`.

Foundry's trace evaluation by agent filter needs the same consistency to group traces correctly. One mechanism satisfies both.

**Security caveat:** baggage propagates across service boundaries including to third parties. IDs only — never PII, tokens or secrets.

### Boundary 7 in detail — the genuinely hard one

Ticketing returns `202 Accepted`; the webhook arrives 40s later with no trace context, because the vendor knows nothing about your traceparent.

```python
async def on_ticketing_webhook(payload):
    corr = await redis.get(f"order:{payload['order_id']}")
    if not corr:                                   # TTL expired or unknown order
        with tracer.start_as_current_span("webhook.orphaned") as s:
            s.set_attribute("resortflow.orphaned_webhook", True)
        return                                     # alert — this is an ops signal
    c = json.loads(corr)
    link = trace.Link(trace.SpanContext(
        trace_id=int(c["trace_id"], 16), span_id=int(c["span_id"], 16),
        is_remote=True, trace_flags=trace.TraceFlags(0x01)))
    with tracer.start_as_current_span("webhook.order_resolved", links=[link]) as s:
        s.set_attribute("resortflow.saga_id", c["saga_id"])
```

A lookup miss is an operational alert, not a quiet log line.

---

## 6. Environment A — Local (Langfuse)

```yaml
# docker-compose.observability.yml
langfuse-web:    { image: langfuse/langfuse:3, ports: ["3000:3000"], environment: { TZ: UTC } }
langfuse-worker: { image: langfuse/langfuse-worker:3, environment: { TZ: UTC } }
clickhouse:      { image: clickhouse/clickhouse-server, environment: { TZ: UTC } }
postgres-lf:     { image: postgres:16, environment: { TZ: UTC } }
redis-lf:        { image: redis:7 }
minio:           { image: minio/minio }
otel-collector:  { image: otel/opentelemetry-collector-contrib }
```

**Timezone is not optional.** All Langfuse infrastructure components must run with timezone set to UTC — non-UTC causes queries to return incorrect or empty results. You are in IST. Pin `TZ=UTC` explicitly or you will lose an afternoon to empty dashboards with no error message.

Storage split: ClickHouse holds traces, observations and scores; Postgres holds users, projects, API keys, prompts and datasets; Redis is the event queue and cache; S3/MinIO holds raw ingestion events and large payloads. Traces are written to S3 first and ingested to ClickHouse by the worker asynchronously — so **traces do not appear instantly**; check the worker container before assuming your instrumentation is broken.

Exporter config: `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3000/api/public/otel`, Basic auth, and the header `x-langfuse-ingestion-version: 4` — without it, directly-ingested OTel data can lag by up to ten minutes. HTTP only; Langfuse does not support gRPC egress.

---

## 7. Environment B — Production (Foundry + Azure Monitor)

Connect an Application Insights resource to the Foundry project. For agents running outside the Foundry Agent Service — which ResortFlow is, on Container Apps — use the **custom agent monitoring** path: register the agent through the Foundry Control Plane, route through AI Gateway, and send OTel traces to the same Application Insights resource used by the Foundry project. From there you get error-rate metrics and continuous evaluation on production traffic.

Evaluation runs over traces already in App Insights without replaying requests — selected either **by trace ID** (passing `operation_Id` values) or **by agent filter**, which discovers recent traces automatically.

Built-in evaluators cover quality, safety and agent-specific dimensions — intent resolution, task adherence, tool accuracy, coherence, relevance — and custom evaluators run in the same pipeline with the same output schema, feeding the same dashboards and alert rules.

### KQL you will actually use

```kusto
// Everything in one turn, across every service
let tid = "4bf92f3577b34da6a3ce929d0e0e4736";
union requests, dependencies, traces, exceptions
| where operation_Id == tid
| project timestamp, itemType, name, duration, resultCode,
          customDimensions.["resortflow.saga_id"], customDimensions.["resortflow.node"]
| order by timestamp asc
```

```kusto
// Reconstruct a Langfuse-style session view (no native equivalent)
customEvents
| where customDimensions.["resortflow.session_id"] == "sess_9f3a"
| summarize turns = dcount(operation_Id),
            first = min(timestamp), last = max(timestamp)
```

```kusto
// The metric that matters most
customEvents | where name == "resortflow.saga.completed"
| summarize count() by tostring(customDimensions.outcome), bin(timestamp, 1h)
| render timechart
// full_success | compensated_success | partial_failure | escalated
```

`partial_failure` must be zero. It's the only outcome where a guest is in an unknown state.

---

## 8. The Twelve Complications

Every one of these is a *silent* failure — the system keeps returning 200s while your observability quietly degrades. That's what makes them worth pre-empting.

---

### C1 — Attribute namespace divergence *(highest risk)*

**Problem.** Langfuse wants `langfuse.observation.type` and `langfuse.trace.metadata.*`; Foundry evaluation wants GenAI semconv `gen_ai.*`. Instrument for one and the other degrades — Langfuse drops unprefixed attributes into a non-filterable `metadata.attributes` catch-all, and Foundry trace evaluation may not parse spans that don't meet its trace data requirements.

**Symptom.** Everything looks fine locally. In production, evaluators return empty or malformed results.

**Mitigation.** Dual-namespace emission in the Telemetry Abstraction Layer (Section 3). Both namespaces on every span, always. Cost is ~8 attributes per span.

**CI gate.** A test that asserts every span emitted by a golden conversation carries both namespaces. Fails the build if a developer sets an attribute directly instead of going through the module.

---

### C2 — Evaluation results are not portable

**Problem.** Langfuse scores live in ClickHouse. Foundry evaluator results live in the Foundry evaluations pane. Different judge models, different metric definitions. Foundry "coherence" is not your RAGAS faithfulness. **You cannot compare a local eval score to a production eval score.**

**Symptom.** "Groundedness dropped from 0.94 to 0.81 in prod" — except it didn't, you just changed measuring instruments.

**Mitigation.** Split the two roles explicitly:
- **Your own portable harness** (RAGAS/DeepEval running in your code) is the *authoritative regression gate*. It runs identically in CI, locally, and against production traces. Results written as scores to whichever backend is present.
- **Platform evaluators** (Foundry built-ins) are *production monitoring and safety*, never the CI gate.

Never let a platform-specific metric gate a merge. This is the single most important design decision in the dual-environment design.

---

### C3 — Sampling divergence

**Problem.** Local Langfuse keeps 100%. App Insights samples adaptively by default. A bug you reproduce locally may have no trace in production.

**Symptom.** Intermittent production issue with no trace to inspect.

**Mitigation.** Disable sampling explicitly on the agent path — evaluation needs complete data. Accept the ingestion cost, and control it via payload externalization (C4) rather than by dropping traces. Keep adaptive sampling on the deterministic non-agentic path where there's no LLM content to observe.

---

### C4 — Payload truncation

**Problem.** Langfuse stores full payloads in blob storage. App Insights truncates custom dimension values. Your reranked RAG context — five chunks — can exceed the cap.

**Symptom.** The nastiest one on this list: **groundedness evaluation silently becomes wrong**, because the judge no longer sees the context the generator saw. Scores drop and you chase a model regression that doesn't exist.

**Mitigation.** Externalize payloads above ~4KB to Blob Storage; put the URI in the span and a truncated preview inline (implemented in Section 3). Evaluators fetch from Blob. Set `resortflow.*.truncated=true` so truncation is *visible* rather than silent.

**Verify the current limit** against your actual chunk sizes before committing — the caps change.

---

### C5 — No native session view in App Insights

**Problem.** Langfuse has first-class sessions — one screen showing every turn of a conversation. App Insights has `operation_Id` per trace and no multi-turn concept.

**Symptom.** A one-click view locally becomes a KQL query in production, so on-call stops using it.

**Mitigation.** `resortflow.session_id` as a custom dimension on every span (already handled by baggage), plus a **saved Azure Workbook** that takes a session ID and renders the turn sequence. Build it once in Phase 7; it's the view you'll use in the demo.

---

### C6 — Prompt management split

**Problem.** Langfuse has a prompt registry with versioning and Redis-backed caching. If you use it locally, production has nothing equivalent wired up.

**Mitigation.** **Don't use Langfuse prompt management at all.** Keep prompts in Git, versioned with the graph, loaded from the repo at startup. Reasons: it's portable, it's diffable in PRs, it ties prompt version to graph version automatically, and it fits your spec-driven development story. A runtime prompt CMS is a genuine feature — it's just the wrong trade for a system where prompt changes should go through code review anyway.

State this as a deliberate choice, not an omission.

> **Superseded by ADR-052** (`docs/adr/052-git-first-prompt-registry-langfuse-mirror.md`). C6's objection is
> addressed, not eliminated: parity is now enforced by CI hash-equality and a runtime hash allow-list rather than
> by physical identity of source. Git stays canonical; Langfuse is a mirror and an optional runtime registry, and
> production defaults to `PROMPT_SOURCE=git`. The original text above is kept for the record.

---

### C7 — PII redaction implemented in the wrong place

**Problem.** Langfuse's SDK offers a `mask` hook. If you implement redaction there, it's Langfuse-specific — the App Insights path leaks.

**Symptom.** Guest names and phone numbers in Log Analytics. In a regulated tenancy that's a reportable incident, not a bug.

**Mitigation.** Redact at **two environment-independent points**: inside the Telemetry Abstraction Layer before any attribute is set, and again as a Collector `transform` processor as backstop. Never rely on a vendor SDK hook.

**Nuance worth raising:** redaction destroys debuggability. Resolution — redact values, preserve structure, use **stable pseudonyms within a session** (`PERSON_a3f`) so you can still verify the agent referred to the same person consistently without storing the name. Dietary free-text is dropped entirely; only the structured enum survives.

---

### C8 — Safety evaluators and red teaming don't exist locally

**Problem.** Foundry's content-harm, jailbreak and adaptive red-teaming capabilities are Azure-only. A whole class of guardrail validation has no local equivalent.

**Symptom.** Prompt-injection and responsible-gaming guardrails are only ever tested in production.

**Mitigation.** Two-part:
- Maintain your **own adversarial corpus** in the portable harness — injection payloads embedded in CMS content, self-exclusion probes, allergen-omission cases. Runs locally, every CI build.
- Schedule Foundry red-team scans against a **dev Foundry project** weekly, not only at release. Budget for one small always-on Foundry project even though the rest is local.

---

### C9 — Preview-surface dependency

**Problem.** Custom agent monitoring — the path ResortFlow needs, since it runs outside the Foundry Agent Service — is in preview. Preview means API changes without notice.

**Mitigation.** Depend on the **stable** layer: OTel spans into Application Insights, which is GA and where the traces live regardless. Treat Foundry's custom-agent registration as an enrichment that can be re-done, not as the data path. If it breaks, you still have complete traces in App Insights and your portable harness still gates CI.

That's a good general principle to articulate: *build on the GA substrate, treat preview features as removable.*

---

### C10 — Environment drift goes undetected

**Problem.** The meta-risk that contains most of the others. "Works locally, silently degraded in production."

**Mitigation.** A **telemetry contract test**. One golden conversation, run in both environments, asserting:
- span count matches within tolerance
- every expected `resortflow.node` value present
- both attribute namespaces present on every span
- no payload marked truncated without a corresponding blob URI
- session grouping resolves to one session in both backends

Run it in CI against local, and as a post-deploy smoke test against production. This is the only thing that catches C1, C3, C4 and C5 before a user does.

---

### C11 — Cost model inversion

**Problem.** Local is free. Production App Insights is priced per GB ingested, and you've disabled sampling (C3).

**Mitigation.** Payload externalization (C4) is also your cost control — large context payloads go to Blob at pennies per GB rather than Log Analytics at premium rates. Set a Log Analytics **daily cap** with alerting, and a budget alert on the resource group. Know your cost per 1,000 turns and be able to state it.

---

### C12 — Two dashboards, nobody looks at either

**Problem.** Operational, not technical, and the most common real-world failure.

**Mitigation.** One primary surface per audience, agreed explicitly: Foundry's Agent Monitoring Dashboard for AI quality, an Azure Workbook for SRE, and alerts that route to the right one. Langfuse is a *development* tool and never appears in a production runbook.

---

## 9. Evaluation Strategy — Portable vs Platform

The clean separation that falls out of C2:

| | Portable harness (yours) | Platform evaluators (Foundry) |
|---|---|---|
| Runs | CI, local, against prod traces | Azure only |
| Metrics | RAGAS/DeepEval, custom saga-correctness, allergen fidelity | Intent resolution, task adherence, tool accuracy, coherence, relevance, safety |
| Role | **Regression gate — blocks merges** | Production monitoring, drift, safety |
| Judge | Pinned model, named in every output | Platform-managed |
| Portability | Total | None |

Both write scores back to whichever backend is present, so the dashboards are unified even though the pipelines aren't.

**The one non-negotiable:** the judge must be at least as strong as the generator. If the evaluator is weaker, your metrics are decorative. This is the single place not to economize on model tier.

---

## 10. PII Redaction

Two enforcement points, both environment-independent (see C7):

```python
def mask_pii(text: str) -> str:
    results = analyzer.analyze(text=text, language="en",
        entities=["PERSON","PHONE_NUMBER","EMAIL_ADDRESS","CREDIT_CARD","LOCATION"])
    return anonymizer.anonymize(text=text, analyzer_results=results,
        operators={"PERSON": OperatorConfig("custom", {"lambda": stable_pseudonym})}).text
```

Stable pseudonym = `PERSON_` + first 3 chars of `HMAC(session_salt, name)`. Consistent within a session, unlinkable across sessions, never reversible to the original.

---

## 11. Sampling

| Category | Rate | Rationale |
|---|---|---|
| Audit records → Cosmos | **100%, never sampled** | Separate path. App Insights samples; a record that may be absent is not an audit record. |
| Turns with mutations | 100% | Money moved |
| Errors, policy blocks | 100% | The interesting ones |
| Low-groundedness turns | 100% | Feed the eval dataset |
| Successful read-only turns | 10% prod / 100% local | Volume control |
| Deterministic non-agentic path | App Insights adaptive | No LLM content to observe |

**Tail-based**, decided in the Collector once the trace completes. Head-based sampling would discard exactly the traces you need, because at the root span you don't yet know the turn will fail. Naming that distinction is a senior signal.

---

## 12. A Debugging Walkthrough

**Alert:** `partial_failure` saga rate jumped 0 → 4% in an hour.

1. **App Insights — is it infrastructure?** Group failures by `resortflow.failed_domain` → all in `dining`. Not systemic.
2. **Which failure mode?** `dependencies | where target contains "dining"` → spike in `410 HOLD_EXPIRED`.
3. **Why?** p95 turn duration rose 4.2s → 11.8s. Holds have 600s TTL, so something takes *minutes*.
4. **Pivot to Foundry on `operation_Id`.** Open the trace. `resortflow.planner` shows `replan_count=3`. Planner is looping.
5. **Root cause.** A CMS refresh removed a venue; Qdrant still served it. Stale grounding → impossible plans → replan loop → hold expiry → partial failure.
6. **Fixes.** Short-term: cap replan at 2, revalidate holds immediately before commit. Long-term: content-hash index invalidation.

**Takeaway:** App Insights localized *where and when*; Foundry explained *why*. Neither alone gets there, which is the concrete justification for the layered design.

---

## 13. Cost Model

**Local:** $0. Docker only.

**Production (demo scale, ~10k turns/mo):**

| Item | Est. |
|---|---|
| App Insights ingestion (sampling off, payloads externalized) | $15–30 |
| Blob storage for payloads | ~$2 |
| Foundry evaluations (judge model tokens) | $10–20 |
| Cosmos audit (serverless) | ~$3 |
| Small dev Foundry project for red-team scans | ~$10 |
| **Total** | **~$40–65/mo** |

Versus $150–270/mo to self-host Langfuse on Azure, with worse safety tooling and a third-party data-residency conversation. The economics and the compliance argument point the same way, which is a comfortable place to be.

**The observation worth deploying:** at low volume the observability stack can cost more than the application it observes, because the app scales to zero and an always-on observability platform structurally cannot. Externalizing payloads and choosing a managed backend is what breaks that.

---

## 14. Design FAQ

**"The production target is all-Azure — why use Langfuse at all?"**
It is not used in production. Foundry Observability is the production backend: GA, OTel-native with GenAI conventions, and its trace-evaluation path explicitly supports LangChain and custom frameworks, plus safety evaluators and adaptive red teaming that would otherwise be built by hand. Langfuse is a local development tool — free, runs on a laptop, no unit meter, fast iteration. Same instrumentation both sides.

**"Doesn't running two backends risk drift?"**
Yes, and it's the main risk. Three mitigations: a telemetry abstraction layer that dual-emits both attribute namespaces so neither backend is second-class; a portable eval harness that is the authoritative regression gate, so platform-specific metrics never gate a merge; and a telemetry contract test that runs the same golden conversation in both and asserts span shape. Without those three, the split is a liability.

**"What's the most dangerous failure mode?"**
Payload truncation. App Insights truncates custom dimensions, so a long RAG context gets cut, and the groundedness judge stops seeing what the generator saw. Scores drop and a team can spend a week chasing a model regression that never happened. The fix is externalizing payloads to Blob above 4KB and flagging truncation explicitly so it's visible rather than silent.

**"Why not gate CI on Foundry's evaluators?"**
Because they only run in Azure and their metric definitions belong to the platform. The gate has to run identically in CI, on a laptop, and against production traces. Platform evaluators are excellent for production monitoring and safety — they're just the wrong thing to block a merge on.

**"How do you correlate an async webhook to the conversation?"**
Correlation store keyed on the vendor's order ID holding trace ID, span ID and saga ID with a TTL beyond the vendor SLA. On receipt, reconstruct the SpanContext and start a linked span. A lookup miss emits an `orphaned_webhook` span and alerts — a missed correlation is an operational signal, not a log line.

**"Parent-child or links for async?"**
Links. The consumer isn't a child of the producer — separate causal operation, different process, often much later. OTel messaging conventions specify links for exactly this, and the same mechanism handles a saga spanning multiple turns.

**"What is the one metric to watch above all others?"**
Saga outcome distribution. `partial_failure` must be zero — it's the only outcome where a guest is in an unknown state. Everything else is a quality conversation; that one is a correctness failure.

**"What would be improved next?"**
Exemplars — linking a p99 latency metric straight to a representative trace rather than hunting for one. And GenAI semantic conventions are still stabilizing, so the attribute mapping is isolated in one module rather than scattering names across the codebase; when the spec moves, it's one file.

---

*Sources: Microsoft Foundry Observability documentation (GA March 2026) and Langfuse v4 OpenTelemetry integration docs, verified September 2026. Attribute names, SDK APIs and service limits shift between versions — re-verify before implementation.*
