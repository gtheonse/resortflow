# ADR-028 — Policy Engine architecture: plain deterministic Python, not a rules engine

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §7 specifies seven enforcement layers (input
screening, entitlement gate, tool schema validation, allergen fidelity, groundedness, PII
redaction, responsible-gaming policy) that must "make it deployable in a regulated
business." `policy/` (Track G) is empty except `.gitkeep`. Two pieces of this logic
already exist, ad hoc, inside domain code: `agents/dining/nodes/allergen_check.py`
(menu-content matching) and `agents/gaming/nodes/lookup.py` (a partial, intent-gated
self-exclusion check). Both are correct as far as they go but neither is reusable,
neither is centrally testable against the full adversarial corpus this phase's exit
criteria require (a 40-turn self-exclusion script, a red-team injection sweep), and
`agents/gaming/nodes/lookup.py`'s check only fires for one of three intents — a concrete
compliance gap, not a hypothetical one.

The question this ADR settles: what *kind* of thing is `policy/` — a rules engine with
its own evaluation semantics, an external policy service, or plain application code.

## Options Considered

**1. Embed a general-purpose rules engine** (e.g., Open Policy Agent as a sidecar
process, or a Python rules library like `durable_rules` evaluating declarative rule
sets). Gives a uniform way to express and change rules without redeploying application
code. Costs: a second deployment artifact (OPA) or a second evaluation semantics to
reason about and test (a rules DSL); a network hop for every policy check on a path with
a sub-1.5-second budget (Constitution Principle I); and, for the safety-critical rules
specifically (allergen fidelity, self-exclusion), a rule expressed as interpreted data is
exactly the "undone by a config edit" risk Constitution Principle IV names — the whole
point of "policy is code, not prompt" is that changing enforcement requires the same
review rigor as changing any other code path, not a lower-friction data edit.

**2. Plain deterministic Python functions**, organized by the seven-layer table, unit-
tested the same way `graph/errors.py`'s `classify()` already is (`classify()` is
explicitly named in the constitution as the model for "determinism first": "an LLM gets
travel-time arithmetic right ~95% of the time; a function gets it right 100% of the time,
in 2ms, and is unit-testable" — the conflict checker from feature 005 is the same
pattern, one project decision already made once). No new deployment artifact, no new
evaluation semantics, ordinary code review governs every rule change.

**3. An LLM-based policy judge** (ask a model whether a given action is permitted).
Rejected outright, not seriously weighed: this is precisely what Constitution Principle
IV forbids ("a rule that can be undone by a prompt edit is not enforced") and what
Principle I's ambiguity-based routing argues against (policy rules here have exactly one
correct answer per input — not unbounded intent).

## Decision

**Option 2.** `policy/` ships as five plain modules —
`policy/gaming.py`, `policy/dietary.py`, `policy/injection.py`, `policy/distress.py`,
`policy/jurisdiction.py` — each exposing the functions in
`contracts/policy-engine-contract.md`, called explicitly from the specific node/subgraph
that needs a decision (gaming lookup, `OFFER_AGENT`, dining allergen check, each domain's
retrieval call site, `POLICY_PREFLIGHT`). No shared "evaluate(rule_set, context)"
indirection layer sits between a caller and the specific function it needs — a caller
imports and calls the one function relevant to its own check, the same way
`graph/errors.py:classify()` is imported and called directly wherever a vendor failure
needs classifying.

The one piece of data-shaped configuration this decision does not foreclose is
jurisdiction message *text* (`policy/jurisdiction.py`'s lookup table) — legal copy that
genuinely varies per tenant with no evaluation logic attached to it, looked up
deterministically, never interpreted as a rule.

## Consequences

**Made easier.** Every policy function is a pure, directly unit-testable Python function
— the same test-writing pattern this project already uses everywhere (`test_reducers.py`,
`test_errors.py`). No new infrastructure (no sidecar to run in `docker-compose`, no new
service to add to `make up`). The 40-turn adversarial script and the injection corpus
sweep can run as ordinary `pytest`/`hypothesis` tests with no external dependency beyond
what already exists.

**Made harder.** A future jurisdiction with meaningfully different rule *structure* (not
just different message text) requires a code change and a review, not a config push —
this is treated as the correct amount of friction for a regulated-business feature, not
an accidental one.

**Foreclosed.** No future feature may introduce a data-driven rule-interpretation layer
for the safety-critical checks (allergen fidelity, self-exclusion, injection screening,
distress routing) without a new ADR explicitly re-opening this question — jurisdiction
message *text* is the one and only place small, tenant-varying data is allowed to live
ungoverned by this restriction, because it carries no evaluation semantics of its own.
