# Contract: Runtime resolution (`prompts.registry`)

The only interface agents and eval code use. No caller imports `langfuse` (a test enforces it).

## API

```
resolve(
    prompt_id: str,
    *,
    required_vars: Mapping[str, str],   # code-injected policy values; win over any caller value (FR-011)
    runtime_vars: Mapping[str, str] = {},
    tenant: TenantProfile | None = None,# closed typed model; only fields declared by the template
) -> ResolvedPrompt

validate_all() -> None                  # process start: every Git template loads, passes V1–V9, is not revoked
```

`required_vars` keys are the mandatory set for this call site (FR-007). For the dining prompt:
`dietary_restrictions = [r.value for r in DietaryRestriction]` and
`dietary_severities = [s.value for s in DietarySeverity]` — rendered from the code enumerations
on every call, never read from the registry or a template.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PROMPT_SOURCE` | `git` | `git` \| `langfuse` |
| `PROMPT_LABEL` | `production` | label for `resolution: label` prompts (`staging` for local trials) |
| `PROMPT_CACHE_TTL_SECONDS` | `60` | SDK cache TTL (stale-while-revalidate) |
| `PROMPT_FETCH_TIMEOUT_SECONDS` | `2` | registry fetch timeout |
| `LANGFUSE_*` | — | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and an explicit `LANGFUSE_HOST` (or `LANGFUSE_BASE_URL`) are all required; any unset ⇒ registry mode behaves as `git-fallback` and no client is constructed. The SDK's default Langfuse Cloud host is never used |

## Source states and what each guarantees

| `source` | Meaning | Served content |
|---|---|---|
| `git` | `PROMPT_SOURCE=git` | template file |
| `langfuse` | registry version, approved, not revoked, variables valid | registry content |
| `git-fallback` | registry error/timeout/malformed, credentials absent, or variable/config check failed | template file |
| `git-fallback-unapproved` | hash ∉ approved | template file |
| `git-fallback-revoked` | hash ∈ revoked | template file |

## Failure matrix (each row is a mandatory test, SC-004)

| Injected fault | Result | `rejected_reason` |
|---|---|---|
| Registry down / timeout | Git copy, turn succeeds | `fetch_error` |
| Registry returns text-type prompt | Git copy | `wrong_shape` |
| Locked hash omits `{{dietary_restrictions}}` (e.g. rollback to an older approved version) | Git copy, WARN | `missing_required_var` |
| Template adds `{{house_notes}}` | Git copy | `undeclared_var` |
| Template references `{{x}}` with no supplied value | Git copy | `unrendered_placeholder` |
| Guest/retrieved text contains `{{x}}` (a runtime value) | served normally, rendered literally | — (no fallback, no error) |
| Config has `model` or unknown key | Git copy | `bad_config` |
| Hash not approved | Git copy | `unapproved` |
| Hash revoked | Git copy | `revoked` |
| Git copy fails V2–V4 / is revoked | **raise** (unreachable in a built image: `validate_all` + CI L4) | — |

## Behavioural guarantees

1. A resolve never blocks a turn longer than the fetch timeout, and never raises for a registry
   or telemetry fault.
2. Code-injected variables are applied last and cannot be overridden.
3. A cache hit does not touch the network (SC-009: ≤ 5 ms median).
4. `exact` prompts (judges) always resolve from Git; no registry lookup is made for them.
5. Guest text appears only as a `runtime_var` in a `user` message; never in a system message
   of a prompt that has tool-calling ability (ADR-032). In-scope prompts have no tools.
6. Runtime values are inserted verbatim in a single pass over the template's placeholders and are never rescanned, so guest or retrieved text containing `{{…}}` cannot cause a rejection or be re-expanded.
