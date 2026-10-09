# Research: Prompt Management (Feature 009)

All Langfuse claims below were verified against current docs **and** the installed SDK
(`langfuse 4.15.2`, read from `.venv/Lib/site-packages/langfuse`) rather than memory. Where a
claim rests on documentation alone it is marked *(docs)*; where the docs are silent it is marked
**UNVERIFIED** (the spike that would have resolved these was withdrawn on 2026-09-26; see R-03).

## R-01 — Langfuse prompt API surface and server compatibility

**Decision:** Use the Python SDK 4.15.x for the prompt API only: `get_prompt`, `create_prompt`,
`update_prompt`, and `api.prompts.list/get`. Pin `langfuse>=4.15,<5`.

**Verified facts**

| Capability | Fact | Source |
|---|---|---|
| Fetch | `get_prompt(name, version=None, label=None, type='text'\|'chat', cache_ttl_seconds=None, fallback=None, max_retries=None, fetch_timeout_seconds=None)` | SDK introspection |
| Default fetch | No label ⇒ the `production` label; none carrying it ⇒ 404. `label` and `version` together ⇒ 400 (mutually exclusive) | *(docs)* version-control |
| Create | `create_prompt(name, prompt, labels=[], tags=None, type='text', config=None, commit_message=None)`; same name ⇒ a **new version** | SDK + docs |
| Move a label | `update_prompt(name=, version=, new_labels=[…])`; "Labels are unique across versions", `latest` is reserved. Invalidates the SDK cache for that name | SDK source `client.py:4198` |
| Rollback | Set `production` on the earlier version | *(docs)* |
| Config | Optional arbitrary JSON, "versioned together with the prompt", read via `prompt.config` | *(docs)* config |
| Client fields | `name, version, config, labels, tags, commit_message, is_fallback` — **no author, no hash** | SDK `model.py` |
| List versions | `api.prompts.list(name=…)` → `PromptMeta{versions:[int], labels, tags, lastUpdatedAt, lastConfig}`; fetch each with `get(name, version=)` | SDK `api/prompts` |
| Cache | Default TTL 60 s; stale-while-revalidate; `cache_ttl_seconds=0` disables; fallback only when cache empty **and** API down | *(docs)* caching |
| Protected labels | Enterprise / Pro only ⇒ **not available on OSS self-hosted** | *(docs)* |
| Server compat | SDK v4 requires self-hosted ≥ 3.63.0; tracing supported on v3 server; use legacy read APIs for observations/metrics (not used here) | *(docs)* versioning |
| Immutability of versions | **Not stated** in docs | **UNVERIFIED**, and nothing in this design depends on it (push creates versions; rollback moves labels) |

**Consequences drawn**

1. Because protected labels are not available on OSS, a UI user with member rights can move
   `production`. The runtime allow-list (spec D2) is therefore the *only* enforcement, not a
   belt-and-braces extra. This strengthens ADR-052 D2.
2. Because there is no server hash and no author, hashing is local (R-05). (Editor credit would
   also have to be supplied by the engineer - S-3, now moot because `pull` is deferred.)
3. `compose` runs `langfuse/langfuse:3`; SDK v4 is compatible. Pin a tested `3.x` tag (R-H).

**Alternatives considered:** raw REST via `httpx` (no SDK) — rejected: reimplements the cache and
`update_prompt` cache invalidation for no gain; SDK v2-style `langfuse.trace()` samples in the
original analysis — do not exist in 4.x.

## R-02 — SDK must not become a second span exporter

**Finding.** `Langfuse()` with tracing on calls `_init_tracer_provider`, may call
`set_tracer_provider`, and adds a `LangfuseSpanProcessor` to the provider (`resource_manager.py`
L242–265, L647–677). In this repo that would double-export spans already sent by ADR-051's
`BatchSpanProcessor` OTLP exporter, or change the global provider.

**Decision:** construct the client as `Langfuse(public_key, secret_key, host, tracing_enabled=False)`
(parameter exists; also `LANGFUSE_TRACING_ENABLED`). With tracing off no processor is attached
and the global provider is untouched. The adapter is the only constructor call site (R-C).

**Alternative:** share our `TracerProvider` via `Langfuse(tracer_provider=…)` and use SDK
generation spans (`start_as_current_observation(as_type="generation", prompt=…)`). Held in
reserve is ADR-052 D5 fallback (i), **not adopted**: D5 was decided as option (ii) on 2026-09-26.

## R-03 — Trace lineage: `resortflow.prompt.*` only (ADR-052 D5, option ii — decided 2026-09-26)

**Background, verified.** Langfuse's OTel mapping table lists `prompt <- langfuse.observation.prompt.name,
langfuse.observation.prompt.version`; SDK 4.15.2 `attributes.py` L52-53 defines those keys and sets them
only when the prompt is not a fallback (L141-145). The endpoint and the `x-langfuse-ingestion-version: 4`
header are already handled by `observability/telemetry.py`.

**Decision.** Emit `resortflow.prompt.{name,version,sha256,source,rejected_reason}` only. Do **not** emit
the Langfuse prompt-link keys, add no propagation `SpanProcessor`, and make no claim that the Langfuse
UI links a generation to a prompt version.

**Why.** (1) The docs do not state that raw-OTel linking renders in the UI. (2) Even if it did, the
link would bind to our wrapper generation span, not the OpenAI-instrumented child that carries
`gen_ai.usage.*`, so the prompt view would show lineage without cost. (3) Making it work would need a
propagation processor and two more frozen attribute names for a behaviour we would then have to
maintain. The cloud-agnostic `resortflow.*` lineage already serves Azure Monitor equally.

**Accepted cost.** Prompt identity (wrapper span) and token cost (instrumented child) are correlated
through the parent span rather than shown together in the Langfuse prompt view.

**Not adopted, noted.** Langfuse documents a `langfuse.observation.metadata.*` mapping that could show
prompt identity as searchable metadata without claiming linkage; it would add attribute names, so it is
left as a possible follow-up.

## R-04 — One compiler for both sources

**Decision.** Do not call `prompt.compile()`. Take `messages` and `config` from the SDK client
(or YAML) and render with our own strict `{{name}}` substitution used by **both** sources.

**Why.** (a) parity: Git and registry render byte-identically; (b) FR-009 needs to know the
template's variable set and to detect a placeholder the template references but no value was supplied for, which SDK `compile` does not
report; (c) undeclared variables (a free-text `{{house_notes}}`) can be rejected (FR-012b);
(d) no HTML/Mustache escaping surprises for quotes in injected enum lists.

**Rules.** Variables match `\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}`. The declared set at the call
site = `required_vars` (code-injected policy values) ∪ `runtime_vars` (data such as question or
context) ∪ tenant vars (subset of `TenantProfile` fields). A template referencing any other name
is rejected; a template missing any `required_var` is rejected. Single-brace JSON in prompt text
(e.g. `{"score": …}`) is left alone. Substitution is a **single pass** over the template's own placeholders: values are inserted verbatim and never rescanned, so guest or retrieved text containing `{{…}}` is rendered literally (it is a value, not a template) and cannot cause a rejection or be re-expanded.

## R-05 — Canonical hash

**Decision.** `sha256` over UTF-8 of
`json.dumps({"type":"chat","messages":[{"role","content"}…],"config":<config>},
sort_keys=True, separators=(",",":"), ensure_ascii=False)` after normalising line endings to LF (universal newlines: CRLF and a lone CR; idempotent, unlike a single-pass CRLF replacement, which Hypothesis showed breaks FR-005 for CR CR LF) in
message content. The hash covers messages **and** config, never name, version, labels, tags or
`commit_message`. No provenance marker lives in `config`: with `pull` deferred there is no consumer
for one, so the hash covers exactly what the config contains. `push` writes an informational
`commit_message` (`resortflow-sync <sha256[:12]>`) that is outside the hash.

**Why.** The registry offers no hash; identical content must hash identically regardless of
source (FR-005). Property tests (Hypothesis) assert key-order and line-ending stability.

## R-06 - Which prompts are label-resolved vs exact (amendment S-2)

**Decision.** Only `dining_intent_extraction` is label-resolved at runtime (in `PROMPT_SOURCE=langfuse`
mode). The five judge templates are `exact`: they always resolve from Git (deterministic, offline, safe
in CI where no registry may exist) and no registry lookup is made for them at all.

**Why.** Registry version numbers differ between registries, so only the hash is portable; with
lineage reduced to `resortflow.prompt.*` (R-03) there is nothing left for a registry lookup to attach.

## R-07 - Fail-closed resolution order

Enforcement is always strict (no staging-serving mode; dropped 2026-09-26). For label-resolved prompts,
per resolve:

```
1. source=git                      -> YAML -> validate(vars, config schema) -> serve            [git]
2. source=langfuse
   a. fetch (cache 60s, timeout 2s) -> error/None/malformed -> git copy                        [git-fallback]
   b. hash in revoked               -> git copy                                                [git-fallback-revoked]
   c. hash not in approved          -> git copy                                                [git-fallback-unapproved]
   d. variable check fails (missing required / undeclared / unrendered / bad config keys)
                                    -> git copy + resortflow.prompt.rejected_reason                  [git-fallback]
   e. else serve                                                                               [langfuse]
3. git copy itself fails (d) or is revoked -> raise (startup validate makes this unreachable in a built image)
```

Step (d) can only trigger for a **locked** hash — for example a rollback to an older approved version that
lacks a variable the call site now requires — because an unlocked hash is already refused at (c).
Revocation (b) and the variable check (d) apply on every resolution. Reason codes for
`resortflow.prompt.rejected_reason`: `fetch_error`, `revoked`, `unapproved`, `missing_required_var`,
`undeclared_var`, `unrendered_placeholder`, `bad_config`, `wrong_shape`.

## R-08 — The dining prompt is not exercised by today's gate (amendment S-5)

**Finding.** `eval.runner` scores retrieval, trajectory and saga behaviour; the dining eval runs
against fakes with the rule-based extractor unless Azure credentials are present. A degraded
dining intent prompt would therefore pass every existing gate, contradicting FR-035.

**Decision.** Add `eval/intent_scorer.py` with two **deterministic** (no judge) metrics over a
labelled utterance set `eval/data/intent_cases.yaml`, run through `AzureIntentExtractor` in the
merge-queue and PR tiers where credentials exist (INCOMPLETE otherwise, per existing semantics):

- `intent_extraction_accuracy` — exact-match on party size, date, time, cuisine.
- `dietary_recall` — fraction of stated restrictions present in the structured payload, with
  severity; floor **1.0** (Principle IV: every stated restriction must appear).

Both are tenant-stratified (FR-021a). The set is new work in Track E and is the largest single
task in this feature apart from the per-tenant gate.

## R-09 — Per-tenant gating on the existing gate

`eval/gate.py` floors use the variance-adjusted mean (mean − 1σ); the delta check uses raw means
and is suspended when `baseline_status != "ok"`. Design: give `MetricScore` an optional
`tenant_id`; evaluate the existing two checks per `(metric, tenant)` and on the aggregate;
extend `Baseline` with per-tenant mean/stdev. A stratum with fewer than `min_cases` (pr=2,
merge_queue=5, nightly=5) yields a coverage failure through the existing coverage verdict, never
a pass. Current data: dining eval is 20 MGV / 5 MHB; `retrieval_cases.yaml` has no tenant tag —
both must be rebalanced and tagged (Phase 3).

## R-10 — `BASELINE_STALE` alongside `BASELINE_INVALID`

`baseline_status_for` today returns `BASELINE_INVALID` on a judge **model** mismatch and the
gate then *suspends* the delta check. `BASELINE_STALE` is a **new hard-fail** verdict reason
raised when any pinned judge-prompt hash differs from `baseline.judge_prompt_hashes` (or a
recorded hash is revoked, FR-016a). `BASELINE_INVALID` semantics are untouched. Precedence in
`compute_verdict`: INCOMPLETE → saga → **BASELINE_STALE** → metrics → coverage. When no
baseline exists (`baseline.json` is `null` today) the gate reports `baseline: absent` explicitly
(FR-021); the first rebaseline populates the hashes.

## R-11 — Rebaseline job mechanics (drives ADR-054)

`.github/workflows/rebaseline.yml`: trigger `pull_request: types [labeled]` where the label is
`rebaseline`, plus `workflow_dispatch`. Steps: check out the PR head ref; run
`python -m eval.runner --tier merge_queue` (eligibility unchanged: COMPLETE only); if
`baseline.json` changed, commit as the bot identity and push to the PR branch; a `concurrency`
group per PR cancels superseded runs. Because the push uses `REBASELINE_TOKEN` (a fine-grained PAT), `synchronize` fires and
the PR gate re-runs; dismiss-stale-approvals then forces owner re-approval (FR-041). Loop-proof:
after the commit, hashes match and a re-run is a no-op. Fork PRs are out of scope (secrets).

## R-12 — Lock, revocations and CI invariants

Two append-only files: `prompts/prompts.lock.json` (approved hashes) and
`prompts/revocations.json` (tombstones), so revocation can be owned by both groups without
loosening approval on the lock. CI invariants (all deterministic, no registry access):

1. every template hash ∈ approved; 2. approved(new) ⊇ approved(base); **2b. approved(new) ⊆
approved(base) ∪ {head template and pinned-judge hashes}** (a hash may be approved only together
with its template; bootstrap with no base lock allows exactly the head hashes; rollback targets
already in approved(base) are unaffected); 3. revocations(new) ⊇ revocations(base) with entries
unchanged; 4. `revoked ∩ current template hashes = ∅` (S-6); 5. `revoked ∩ approved-and-pinned-judge
hashes = ∅`; 6. if the PR carries the break-glass marker, `approved(new) == approved(base)` (no new
content, FR-040); 7. lint clean; 8. CODEOWNERS block equals generation from the FR-042 rules
(last-match-wins, default `/prompts/` -> compliance); 9. every referenced owner group in `owners.yaml`
has ≥ 2 members (FR-038/FR-039). **`base` is `git merge-base HEAD <target>`** (not `HEAD~1`), so pull
requests and the merge queue agree.

## R-13 - Break-glass under CODEOWNERS (audit job dropped 2026-09-26)

CODEOWNERS is "any listed owner satisfies the rule". `prompts/revocations.json` is owned by both the
compliance group and the engineering incident group so either can approve. A break-glass revocation
merges on approval by the incident-responder group (or compliance) plus an incident label; the replacement must be an already-approved hash
(CI invariant L6: no approved hash added in the same change). The owner's after-the-fact approval is
recorded manually in the tombstone's `owner_ack`. **There is no audit job:** an hourly `revocation-audit`
that flagged overdue acknowledgements was designed and dropped, so the acknowledgement window is a
documented convention, not an enforced control. ADR-052 D11 says so.

## R-14 — Naming: no folders, no composability

Langfuse "folders" use `/` in names; langfuse issue #9354 reports prompts with folder names not
being associated with traces. Composability resolves referenced prompts server-side, which would
make the served text differ from anything we hash. Both rejected; names are `snake_case`.

## R-15 — Deployment surface

Production default stays `PROMPT_SOURCE=git`: the templates and lock are files in the image, no
network dependency, no new infra. Registry mode is opt-in per environment. `make seed` gains a
`prompts-sync` step (push + lock) for the local stack.

## Alternatives rejected at the feature level

| Alternative | Why not |
|---|---|
| Keep C6 — Git only, no registry | Loses label rollback and the registry UI; spec D1 chose hybrid |
| Langfuse as source of truth | UI edits would bypass the CI gate (constitution: degraded prompt MUST fail CI) and Principle IV |
| Registry-pushed revocation list (hot path) | A second trust channel for one feature; expedited deploy chosen (spec Q2) |
| Per-tenant templates / base+partial composition | Jurisdiction wording in prompt text; rejected (spec Q4) |

## Deferred by decision (2026-09-26)

The registry-to-Git **`pull`** import was specified during clarification and then deferred
entirely. What was designed and is now future work (ADR-052 D9): a three-way compare against a
recorded `derived_from` hash (stored in `config._resortflow` and `commit_message`), an explicit
`--discard` before superseding unpulled registry versions with an actor-logged Discard Record,
rejection of literal dietary values and config changes at import time, and a co-author trailer
crediting the registry editor. Findings that should survive for whoever builds it: the registry
exposes no author field (S-3); whether a marker survives a UI edit is unverified (S-4); and
`config` is versioned with the prompt, which makes it the most plausible carrier.

The CI-side lint for literal dietary values (FR-028e) is kept: it protects Principle IV
independently of any import tool.

## R-16 — `dietary_recall` calibration (T053a, 2026-09-27, live Foundry judge)

**Method.** `eval.intent_scorer.score_intent_cases` was run against `eval/data/intent_cases.yaml`
(24 cases, both tenants) through the real `AzureIntentExtractor` (Foundry endpoint, the deployment
pinned in `.env`) — first 20 times, then 5 more confirmation runs after the fix below — at
`temperature=0`, no mocking.

**First pass (20 runs, pre-fix).** Fully deterministic: `dietary_recall` mean 0.9167, **sigma
0.0000** across all 20 runs — this is not run-to-run flakiness, it is a consistent, systematic
result. Two cases (`mgv-02`, `mhb-02`) missed a stated restriction on every single run.
`intent_extraction_accuracy` (a separate metric, unaffected by this finding) was 0.7396–0.7500
across runs — also worth watching, but out of scope for this task's floor.

**Root cause.** Both cases stated an unqualified severity ("shellfish allergy", "egg allergy", no
"moderate"/"severe" qualifier). The fixture expected `allergy_moderate` (the RULE-BASED extractor's
own documented default for an unqualified mention — `agents/dining/nodes/intent_parser.py`'s
`RESTRICTION_KEYWORDS` table). The real judge model has no such rule; asked to classify the same
unqualified text, it consistently resolved it to `allergy_severe` — the safer of the two
allergy-severity values under Principle IV, not an error. A follow-up probe confirmed the model
correctly distinguishes the two severities when the guest is actually explicit: "moderate shellfish
allergy" → `allergy_moderate`; "mild shellfish allergy" → `allergy_moderate`; "severe X allergy" →
`allergy_severe`. The ambiguity was in the TEST DATA's assumption that a rule-based heuristic and an
LLM's judgment agree on an unstated case, not in the model or the prompt.

**Decision (per the rule this task's spec records).** Fixed the two ambiguous cases rather than the
prompt: `mgv-02`'s guest text now says "moderate shellfish allergy" (explicit, keeps
`allergy_moderate` covered in the fixture); `mhb-02` keeps its original unqualified "egg allergy"
text and its expected severity was corrected to `allergy_severe`, with a comment recording why.

**Confirmation pass (5 runs, post-fix).** `dietary_recall` mean **1.0000**, sigma **0.0000**, zero
cases ever missed a stated restriction, including the allergy-severity-only subset. Clean.

**Outcome.** `DIETARY_RECALL_BLOCKING = True` (`eval/runner.py`) in the same change. `dietary_recall`
now blocks a merge at its 1.0 floor.
