# ResortFlow Cloud Portability Review — AWS / GCP / Azure

## 1. Purpose & scope

This is a **paper review of design-time portability**, not a migration and not
a working three-cloud deployment. It was written at Phase 0, when `agents/`, `graph/`,
`mcp/`, and `sims/` held real Dockerfiles and contracts but almost no
application code. Nothing in those directories needs to change for this
review — the coupling to Azure found below lives almost entirely in
`docker-compose.yml`, the five frozen OpenAPI vendor contracts, one telemetry
code sample inside a doc, and prose in `AGENTS.md`, the main plan, and two
ADRs.

That asymmetry is itself the first finding: **the real code is closer to
cloud-neutral than the documentation is.** LangGraph, Postgres,
`EncryptedSerializer`, Qdrant, and the MCP layer contain no vendor SDK calls.
The Azure commitment is concentrated in configuration, contract URLs, and
narrative framing — which is good news, because it means most of Tier 2 below
is a documentation fix, not a rewrite.

## 2. Thesis

The project already contains the argument this review needs. ADR-004
(Postgres over Cosmos DB) rejects an Azure-native checkpointer specifically
because "engine parity between local and cloud was judged worth more than
consolidating datastores," and states the underlying principle directly:

> "if a behavior only reproduces in Azure, the abstraction is wrong"

ADR-003 (Qdrant over Azure AI Search) applies the same discipline to the
vector store: keep the portable, self-hostable engine in both places, and
treat the Azure-native managed service as *documentation* of a production
option, not an implementation dependency.

Both ADRs stop at the **local-vs-Azure** boundary. This review's only new
move is extending that same rule across a second and third axis — AWS and
GCP — rather than introducing a different philosophy: *a design where only
one cloud reproduces the behavior is a defect, regardless of whether the
other clouds are ever actually deployed.*

## 3. Method

Every subsystem below is classified into one of three tiers:

- **Tier 1 — Hard-wired.** A literal string, image, or SDK call ties the
  system to Azure specifically. This breaks on another cloud today, not just
  in theory.
- **Tier 2 — Framing-only.** The underlying engine or mechanism is already
  cloud-neutral (self-hosted, open protocol, or env-var-driven); only the
  *documented production target* or prose narrows it to Azure.
- **Tier 3 — Platform-only, by design.** A genuine Azure premium feature with
  no equivalent worth forcing on the other two clouds. Not a gap — a
  deliberate non-goal.

## 4. Per-subsystem table

| Subsystem | Current (Azure) | Tier | AWS equivalent | GCP equivalent |
|---|---|---|---|---|
| LLM / model provider | Azure OpenAI, gpt-4.1-mini default / gpt-4.1 for planner/recovery/judge (`AGENTS.md`; plan.md §10: "The one thing worth paying for. Provider abstraction allows Ollama locally for offline dev.") | **1** | Amazon Bedrock (Claude family) | GCP Vertex AI (Gemini family) |
| Vector store | Qdrant in Docker for build; **Azure AI Search documented as the production target, not implemented** (ADR-003) | **2** | AWS OpenSearch Service (k-NN plugin), or Qdrant Cloud on AWS | GCP Vertex AI Search, or Qdrant Cloud on GCP |
| Checkpointer / relational store | Postgres 16 in Docker; Azure Database for PostgreSQL Flexible Server, Burstable B1ms, in Azure (ADR-004, plan.md §10 "Same engine both sides") | **2** | Amazon RDS for PostgreSQL, or Aurora PostgreSQL | GCP Cloud SQL for PostgreSQL |
| Blob storage | `azurite` (`mcr.microsoft.com/azure-storage/azurite:latest`) in the **default** compose service list → Azure Storage in production (plan.md §10) | **1** | Amazon S3 (local: MinIO) | GCS (local: MinIO, or fake-gcs-server) |
| Async messaging | `servicebus-emulator` + `servicebus-emulator-mssql` (MSSQL-backed) in the **default** compose service list → Azure Service Bus Standard, "Sessions needed for ordered guest events" (plan.md §12) | **1** | Amazon SQS FIFO (message group ID) or Amazon MQ (RabbitMQ) | GCP Pub/Sub with ordering keys |
| Observability / tracing | Langfuse (self-hosted, OTel) locally → Microsoft Foundry Observability + Azure Monitor / Application Insights in production (`ResortFlow-Observability-Tracing-Deep-Dive.md`) | **1 for one attribute, 2 for the pipeline** | OTel Collector → AWS X-Ray + CloudWatch | OTel Collector → GCP Cloud Trace + Cloud Monitoring |
| Eval backend | Portable RAGAS/DeepEval harness (runs anywhere) + Foundry platform evaluators / adaptive red-teaming (Azure-only) | **2 for the harness, 3 for the platform layer** | Bedrock Guardrails covers adjacent, not identical, ground | Vertex AI Gen AI evaluation service, same caveat |
| Identity / auth | Local JWT issuer in dev → Microsoft Entra ID / B2C in production ("Dev tokens carry the same claims shape... so no code differs," plan.md §10) | **2** | Amazon Cognito | GCP Identity Platform / Firebase Auth |
| API gateway | FastAPI edge-shim in dev → Azure APIM in production; **all five frozen OpenAPI contracts hardcode `https://apim-resortflow.azure-api.net/...` as a literal `servers:` entry** | **1** | Amazon API Gateway | GCP Apigee / Cloud Endpoints |
| Compute / runtime | Docker Compose in dev → Azure Container Apps, scale-to-zero, explicitly chosen over AKS ("AKS is the wrong call at this scale," plan.md §10/§12) | **2** | AWS App Runner (closest scale-to-zero analog), or ECS Fargate | GCP Cloud Run |
| Secrets / KMS | Azure Key Vault named as the key source, injected as an env var by Container Apps (`ResortFlow-HITL-Durability-State.md` §5) | **2** | AWS Secrets Manager, or Parameter Store | GCP Secret Manager |
| Async compute | Azure Functions, named only in the architecture diagram for webhooks/embedding refresh — not implemented | **2 (nothing built yet)** | AWS Lambda, or skip FaaS entirely — Cloud Run jobs/App Runner already fit ResortFlow's containers-everywhere pattern | GCP Cloud Functions, or Cloud Run jobs |
| IaC | None exists yet. Phase 8 already names **Terraform** as the tool: "Terraform: resource groups, Container Apps env, APIM, Postgres, Service Bus, Key Vault, Entra app registrations" (plan.md §16) | **2 (opportunity, not debt)** | Same Terraform, AWS provider | Same Terraform, GCP provider |

The IaC row deserves a correction to the initial hypothesis: the plan
**already commits to Terraform**, not a single-cloud tool like Bicep or
CloudFormation. That is the right call already made — Terraform's HCL and
provider model are the one IaC approach in this list that targets all three
clouds without a rewrite. The actual gap is that the resource list itself
(`Container Apps env, APIM, ... Entra app registrations`) is written in
Azure-specific nouns; the fix is a parallel `variables.tf`-level contract with
per-cloud modules, not a change of tool.

## 5. Deep-dive callouts

**LLM provider.** Be honest about what actually ports: you cannot deploy
literally "gpt-4.1" to Bedrock or Vertex AI. What ports is the *pattern* —
a provider interface (endpoint + auth strategy + model-name mapping), the
static tiering discipline (`docs/ResortFlow-Model-Tiering.md`), and "the judge must
be ≥ the generator" rule. Portability here means the abstraction and the
policy survive a provider swap, not that the specific model does.

**Async messaging.** SQS FIFO's message-group ID and Pub/Sub's ordering key
are the direct analogs of a Service Bus session, but they are not
semantically identical — throughput ceilings and exactly-once delivery
windows differ per service. Plan.md §10 already lists "RabbitMQ" as an
alternative to the Service Bus emulator, never implemented. RabbitMQ (or
managed Amazon MQ for RabbitMQ) is the one option with genuinely identical
session/ordering semantics locally and on all three clouds — worth adopting
as the actual default rather than leaving it as an unexercised footnote.

**API gateway.** The three managed options (APIM, API Gateway, Apigee) are
not equally easy to reason about side by side — each has its own
policy-configuration language. A self-hosted gateway (Kong or Envoy), run
identically in Docker locally and as a container in production, extends the
exact "engine parity" argument ADR-004 already made for Postgres, and is a
smaller conceptual step than today's hand-rolled `edge-shim` — putting Kong
or Envoy in front of it, or replacing it outright, costs less than adopting
and maintaining three different managed-gateway policy languages. The honest
trade-off: this forfeits each cloud's native identity integration (APIM +
Entra ID, API Gateway + Cognito, Apigee + Identity Platform) in exchange for
one configuration that works everywhere.

**Observability.** The design is already close to right: the telemetry
abstraction layer is meant to be "the only place in the codebase where an
attribute name appears," dual-emitting `gen_ai.*` and `langfuse.*`
namespaces, and the doc's own stated mitigation is "select the exporter from
an environment variable." The one concrete defect is this line in the
telemetry sample:

```python
span.set_attribute("gen_ai.system", "az.ai.openai")
```

This hardcodes the provider identity instead of deriving it from the active
LLM provider configuration — it should read whatever `ModelProvider` (§5,
LLM row) is actually configured, not always claim Azure. Once fixed, the
actual cloud-swap point becomes an OTel Collector export config (AWS: X-Ray +
CloudWatch exporters; GCP: Cloud Trace + Cloud Monitoring exporters), not
application code.

**IaC.** No correction needed to the tool choice (Terraform already), only to
the resource vocabulary — see §4 above.

## 6. Severity classification

**Tier 1 — hard-wired, breaks on another cloud today.** Only these four items
should generate follow-up work:
1. All five OpenAPI contracts' hardcoded `servers:` URL
   (`https://apim-resortflow.azure-api.net/...`) — **still open**.
2. `azurite` + `servicebus-emulator`/`servicebus-emulator-mssql` sitting in
   the **default** (no-profile) `docker-compose.yml` service list — every
   `make up` pulls Azure-specific emulator images even for contributors who
   will never touch Azure — **still open**.
3. ~~The hardcoded `gen_ai.system = "az.ai.openai"` telemetry attribute~~ —
   **closed by feature 007** (`observability/telemetry.py`, ADR-041):
   `gen_ai.system` is read from `LLM_PROVIDER` at runtime; the literal never
   made it into code in the first place (the sample above was doc-only).
4. ~~No LLM-provider abstraction exists in code~~ — **closed for the eval
   judge client specifically by feature 007** (`eval/judge.py`, ADR-044
   amended): `OpenAICompatibleJudgeClient` selects provider/endpoint/auth via
   configuration, not a hardcoded SDK call. **Still open** for every other
   existing node's direct Azure-OpenAI-client call (e.g.
   `agents/dining/nodes/intent_parser.py`) — that remains a project-wide
   migration this feature deliberately did not undertake (see ADR-044's
   Consequences).

**Tier 2 — engine already portable, only the documented target is
Azure-exclusive.** Needs doc-prose fixes only, no code change: Postgres,
Qdrant, containers/compute, env-var-sourced secrets, identity claims shape,
the OTel pipeline itself, the Terraform tool choice (just not its resource
vocabulary).

**Tier 3 — legitimately Azure-only premium features. Document as optional,
not a gap:** Foundry Agent Service, Foundry adaptive red-teaming, Foundry
safety evaluators, KQL/Log Analytics queries and Azure Workbooks, PTU
(provisioned throughput — Bedrock and Vertex AI have their own distinct
throughput-guarantee products, not a 1:1 port).

## 7. What this review is not claiming

- Not a working multi-cloud CI matrix — nothing here has been deployed to or
  tested against AWS or GCP.
- Not a claim that Tier 3 features have equivalents elsewhere — they
  deliberately don't, and that's fine.
- Not a rewrite of any accepted ADR — ADR-003 and ADR-004 stay Accepted; this
  review extends their reasoning, it doesn't reverse it.

## 8. Recommended remediation sequencing

Tier 1 items only, in priority order, each a **candidate future SpecKit
feature** — none spec'd out here:

1. Parameterize the OpenAPI `servers:` blocks (or drop the hardcoded
   production URL from the frozen contracts entirely, keeping only the local
   entry plus a placeholder)
2. Replace `azurite` and the `servicebus-emulator`/`servicebus-emulator-mssql`
   pair in the default compose topology with MinIO (S3/GCS-compatible) and
   RabbitMQ respectively
3. ~~Fix the hardcoded `gen_ai.system` telemetry attribute to derive from the
   active provider config~~ — **done, feature 007**.
4. Introduce an LLM-provider abstraction (interface + Azure OpenAI / Bedrock
   / Vertex AI adapters), building on the tiering logic already specified in
   `docs/ResortFlow-Model-Tiering.md` — **done for the eval judge client, feature
   007** (`eval/judge.py`); **still open** for every other node's LLM call.
5. Extend the Terraform resource vocabulary with per-cloud modules

Per `AGENTS.md`'s working agreement, any of these goes through
specify → clarify → plan → tasks → analyze → implement → converge — this
review stays analysis-only and dispatches nothing directly.

## Appendix A — Evidence

| File | Location | Quote | Tier |
|---|---|---|---|
| `docker-compose.yml` | line 74 | `image: mcr.microsoft.com/azure-storage/azurite:latest` | 1 |
| `docker-compose.yml` | line 83 | `image: mcr.microsoft.com/azure-messaging/servicebus-emulator:latest` | 1 |
| `docker-compose.yml` | line 93 | `image: mcr.microsoft.com/mssql/server:2022-latest` (Service Bus emulator's backing store) | 1 |
| `docs/contracts/openapi/pms-sim.openapi.yaml` | line 16 | `- url: https://apim-resortflow.azure-api.net/pms/v5` | 1 |
| `docs/contracts/openapi/dining-sim.openapi.yaml` | line 22 | `- url: https://apim-resortflow.azure-api.net/dining/v1` | 1 |
| `docs/contracts/openapi/ticketing-sim.openapi.yaml` | line 22 | `- url: https://apim-resortflow.azure-api.net/ticketing/discovery/v2` | 1 |
| `docs/contracts/openapi/casino-sim.openapi.yaml` | line 20 | `- url: https://apim-resortflow.azure-api.net/casino/v1` | 1 |
| `docs/contracts/openapi/amenities-sim.openapi.yaml` | line 16 | `- url: https://apim-resortflow.azure-api.net/amenities/v3` | 1 |
| `docs/ResortFlow-Observability-Tracing-Deep-Dive.md` | telemetry sample | `span.set_attribute("gen_ai.system", "az.ai.openai")` | 1 — **closed, feature 007** |
| `AGENTS.md` | tech stack | "LLM: Azure OpenAI — gpt-4.1-mini default, gpt-4.1 for planner/recovery/judge" | 1 |
| `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` | §10 | "LLM \| **Azure OpenAI** — gpt-4.1-mini default, gpt-4.1 for frontier nodes \| Same \| The one thing worth paying for. Provider abstraction allows Ollama locally for offline dev." | 1 |
| `docs/adr/003-qdrant-vs-azure-ai-search.md` | Decision | "Qdrant in Docker for build; Azure AI Search documented as the production target, not implemented in this repository." | 2 |
| `docs/adr/004-postgres-checkpointer.md` | Decision | "Postgres, the same engine locally and in Azure... if a behavior only reproduces in Azure, the abstraction is wrong." | 2 |
| `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` | §16 | "Terraform: resource groups, Container Apps env, APIM, Postgres, Service Bus, Key Vault, Entra app registrations" | 2 |
| `docs/ResortFlow-Observability-Tracing-Deep-Dive.md` | §7 KQL example | `union requests, dependencies, traces, exceptions` | 3 |
| `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` | §12 | "PTU documented as production answer for latency guarantees" | 3 |

## Appendix B — Service-equivalence caveats

- **Service Bus sessions vs. SQS FIFO message groups vs. Pub/Sub ordering
  keys**: all three provide ordered, grouped delivery, but throughput
  ceilings and exactly-once delivery windows are not identical across the
  three services. Don't treat these as drop-in replacements without
  re-verifying the specific ordering/throughput guarantee ResortFlow's guest-event
  flow needs.
- **Bedrock / Vertex AI vs. Azure OpenAI**: model family changes, not just
  the vendor. `gpt-4.1` / `gpt-4.1-mini` have no literal equivalent outside
  Azure OpenAI — a cross-cloud deployment means re-running the model-tiering
  evaluation (`docs/ResortFlow-Model-Tiering.md`) against Claude or Gemini models,
  not just repointing an endpoint.
- **PTU vs. Bedrock Provisioned Throughput vs. Vertex AI Provisioned
  Throughput**: each cloud's latency-guarantee product has its own
  commitment terms and unit pricing; none are interchangeable line items in a
  cost model.
