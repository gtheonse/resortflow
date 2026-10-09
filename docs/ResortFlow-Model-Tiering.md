# ResortFlow — Runtime Model Tiering: Design Rationale

ResortFlow uses two separate tiering vocabularies that share a principle and nothing else:

| | Build-time | Runtime |
|---|---|---|
| Question answered | Which Claude model executes a given spec-kit task | Which Azure OpenAI model a graph node calls for a guest |
| Models | Opus / Sonnet / Haiku | gpt-4.1 / gpt-4.1-mini |
| Cost borne by | The developer's subscription | Per guest conversation |
| Selection | Per task, at authoring time | Per node, at design time |
| **Dynamic?** | No — declared in `tasks.md` | **No — declared per node** (see below) |
| Documented in | [`models.md`](models.md) | [`model-tiering-runtime.md`](model-tiering-runtime.md) |

Keep the two documents and vocabularies separate. This file explains the *reasoning* behind
the runtime policy; [`model-tiering-runtime.md`](model-tiering-runtime.md) is the live
register of which node uses which tier.

---

## 1. Why runtime selection is static, not dynamic

The alternative considered was choosing a model per request, by predicted complexity. It was
rejected for four reasons:

1. **The node already encodes the complexity.** That is what decomposing the system into a graph
   buys. The Planner does multi-step reasoning; the Dining subgraph selects among six tools with
   the hard part resolved upstream. A runtime classifier would re-derive at request time what the
   design already decided.
2. **A classifier call costs latency and money on every turn**, and adds a failure mode: a hard
   request misrouted to the cheap model yields a bad plan, silently.
3. **Evaluation becomes unstable.** If model selection varies per request, eval runs are not
   comparable and a regression cannot be attributed. The CI gate stops meaning anything.
4. **Incident debugging gets worse.** "Why did this conversation fail?" gains a second question:
   "which model actually ran?"

## 2. Static assignment

| Node | Tier | Rationale |
|---|---|---|
| Intent router | `gpt-4.1-mini` (a trained classifier, or no LLM at all, is also valid) | High volume, narrow task |
| Domain subgraphs | `gpt-4.1-mini` | Constrained tool selection within one domain |
| Planner | `gpt-4.1` | Genuine multi-step decomposition under constraints |
| Recovery | `gpt-4.1` | "What is the best partial outcome" is a judgment call |
| Groundedness judge | `gpt-4.1` | The judge must be at least as capable as the generator |
| Offer agent | `gpt-4.1-mini` | Bounded suggestion task |
| Conflict checker | **none** | Arithmetic; deliberately deterministic |

The design estimate is that roughly 80% of node executions run on the economy tier, for about a
5x cost reduction against routing everything to the frontier tier, with no expected quality loss
on the constrained nodes. Treat these figures as planning estimates; the live cost per
conversation is measured by the eval suite.

## 3. The one permitted dynamic pattern: bounded escalation

The policy does not *predict* complexity; it *detects failure and retries higher*:

```
Domain subgraph attempts on gpt-4.1-mini
  → validator checks (tool args valid? plan satisfies constraints?)
    → pass: proceed
    → fail: retry once on gpt-4.1, tagged escalated=true
      → fail again: escalate to a human
```

This is measured rather than predicted. The escalation rate is a first-class metric: if a node
escalates more than about 5% of the time, its base tier is wrong and should be promoted
permanently.

**No downward escalation.** A node is never demoted at runtime. The judge, planner, and policy
paths never participate in escalation: they start at the frontier tier and stay there.

> As of this writing no node escalates at runtime; every economy-tier node falls back to a
> deterministic path instead (see the register in `model-tiering-runtime.md`). Escalation is
> specified here as the only permitted dynamic behavior, should a node need it.

## 4. Relationship to spec-driven development

- **Declared tier.** The constitution's *Declared Model Tier* principle requires every LLM-invoking
  node to declare its runtime tier statically, and every implementation task to declare a
  build-time tier.
- **Cost as an acceptance criterion.** Runtime-phase specs can state a ceiling on cost per
  completed multi-domain booking conversation, verified in the eval suite rather than discovered
  on a cloud bill.
- **Tier assignment doubles as spec review.** A task that cannot be assigned to the economy tier
  because "the model needs to figure out X" is a specification gap found before implementation.
- **Known risk: tier drift.** Under schedule pressure, tasks drift down a tier. The `[CRIT]` list
  in `models.md` plus the `/speckit-analyze` check is the guard.
