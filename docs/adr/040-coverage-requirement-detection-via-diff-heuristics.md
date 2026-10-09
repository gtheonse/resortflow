# ADR-040 — Coverage-requirement detection via diff heuristics, not a new registry

**Status**: Accepted
**Date**: 2026-09-13
**Feature**: 007-eval-observability-gate

## Context

FR-009a requires the evaluation gate to fail closed whenever a change introduces
or modifies a capability that needs eval coverage — a tool declaring a `commit`
or `hold` entitlement, a new saga participant or compensation path, a
tenant-scoped content corpus, or a policy rule — and to derive that requirement
**automatically** from what the change actually touches, not from a separately
maintained list that can go stale.

Research into the current codebase found four different structural realities:
tool entitlements are declared consistently via a `@register_tool(...)` decorator
(`mcp/gateway/_shared/registry.py`) across five domain modules; tenant corpora
are enumerated by directory structure (`content/corpus/{tenant}/*.yaml`); saga
participants and compensation logic are centralized in `graph/supervisor.py` but
embedded in dicts and one large `recovery()` function, not a declarative list;
and policy rules are, by ADR-028's explicit design, "ordinary, unit-testable
functions" with no shared registry or indirection layer at all.

## Options Considered

**1. A new central capability manifest.** Every commit/hold tool, saga
participant, and policy rule registers itself in one new YAML/Python file that
the gate diffs against. Advantage: precise, uniform detection logic regardless
of capability type. Disadvantage: duplicates a source of truth that already
exists for tools and corpora (the decorator, the directory layout), and directly
reopens ADR-028's deliberate decision that policy rules carry no shared registry
— a manifest can also silently drift from the code it describes, the exact
failure mode FR-009a exists to prevent.

**2. Git-diff heuristics tailored per capability type**, using whatever
structural signal already exists for that type: decorator/regex scan for tool
entitlements, path glob for tenant corpora, path+function-boundary heuristics
for saga/compensation and policy rules. Advantage: no new source of truth,
respects ADR-028's design, ships this phase using only existing structure.
Disadvantage: the saga/policy heuristics are file- or function-granular rather
than precise — a change to an unrelated function in `policy/dietary.py` still
trips the gate.

**3. AST-level function-granularity diffing for every capability type**,
including saga/policy. Advantage: eliminates option 2's false positives entirely.
Disadvantage: meaningfully more implementation complexity (a general-purpose
Python AST diff-and-classify pass) to buy a reduction in false positives, not
false negatives — the gate's correctness does not depend on this precision, only
its friction does.

## Decision

**Adopt Option 2.** Coverage requirements are detected via git-diff heuristics,
matched to whichever structural signal already exists for that capability type
(decorator scan, path glob, or file/function-path heuristic), with no new
central manifest introduced.

## Consequences

**Made easier.** No second source of truth to keep in sync with the code; ships
this phase using structure that already exists; stays consistent with ADR-028's
explicit rejection of a policy-rule registry.

**Made harder.** An unrelated change to `graph/supervisor.py` or any `policy/
*.py` file will sometimes trip the coverage gate when no real saga/policy
capability changed — engineers will occasionally need to file a Coverage
Exemption (or add a case) for a change that, on inspection, needed neither.

**Foreclosed.** This ADR does not implement function-granularity precision for
saga/policy detection (Option 3) — that remains a documented future refinement,
not a defect in what ships now.
