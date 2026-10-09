"""Track G — the Policy Engine (feature 006, ADR-028).

Plain deterministic Python, not a rules engine: every rule in this package is
an ordinary, unit-testable function organized by the seven-layer enforcement
table in ``docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`` §7. No embedded rules
engine, no policy sidecar, no LLM call anywhere in this package (Constitution
Principle I / IV).

Call surface: ``contracts/policy-engine-contract.md``
(``specs/006-policy-guardrails-hitl/``). Each module below is called
explicitly by the specific node/subgraph that needs a decision — there is no
shared ``evaluate(rule_set, context)`` indirection layer.
"""

from __future__ import annotations
