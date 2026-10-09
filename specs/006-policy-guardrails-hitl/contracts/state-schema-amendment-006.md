# State schema amendment 006 — `policy_flags` reducer, no new keys

Companion to `contracts/state-schema-amendment-005.md`'s precedent (that amendment added
three new keys; this one changes zero keys and one type annotation).

## Change

```diff
- policy_flags: list[PolicyFlag]
+ policy_flags: Annotated[list[PolicyFlag], append_only]
```

`append_only` is the existing reducer already imported into `graph/state.py` and used by
`confirmations`, `failures`, `saga_log` — no new reducer function is introduced.

## Why this still needs an ADR (ADR-029) despite being a one-line diff

`ResortFlowState`'s own docstring: "Adding, removing or renaming a key is a breaking change
requiring an ADR — never a routine edit." A reducer-annotation change is not a key
add/remove/rename, but it changes the channel's runtime merge behavior from
last-write-wins to accumulate — a silent behavior change for any code that (incorrectly)
assumed the old semantics. Given `policy_flags` has never been written to by any node
(research.md Discovery B), no such code exists to break, but the change still meets the
significance bar (a competent engineer could reasonably have left it unannotated and
handled accumulation with an explicit merge elsewhere) and is recorded per the project's
"every decision is documented" standard rather than folded silently into an unrelated
commit.

## Compatibility

Additive in the durability sense: `ResortFlowState.__init__` call sites across every domain
already pass `policy_flags=[]`; an empty list reduces identically under either the old
or new channel semantics, so no existing checkpoint or test fixture requires updating.
`tests/test_reducers.py`'s three reducer properties (accepts absent left, idempotent
under replay, loses nothing from concurrent writers) apply to `policy_flags` exactly as
they already do to `failures`/`saga_log` — this feature adds a
`test_policy_flags_reducer` test alongside the existing reducer test suite, not a new
test module.
