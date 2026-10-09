"""T039/T040 — per-step cost/latency budget checking (FR-004/FR-005, US4).

`eval.conversation_record.build_conversation_record`'s own docstring
discloses that its `cost`/`latency_ms` fields depend on a `SpanLookup` hook
with no live backend to query yet. Rather than block US4 on that gap, this
module computes budget-relevant actuals directly from whatever a caller
already measured around a tool call or domain run (a plain `{scope:
{"cost": x, "latency": y}}` mapping) and compares them against declared
`Budget` thresholds — independent of the trace backend.

Usage (the shape `eval/runner.py`'s tier wiring is expected to call):

    from eval.cost_latency import check_budgets, load_budgets

    budgets = load_budgets()  # eval/data/budgets.yaml
    actuals = {"dining.hold_table": {"cost": 0.004, "latency": 850.0}}
    run.budget_flags = check_budgets(actuals, budgets)

`check_budgets` returns `list[BudgetFlag]`, `EvaluationRun.budget_flags`'s
declared type (`eval/models.py`) — directly assignable, no adaptation layer
needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from eval.models import BudgetFlag

_BUDGETS_PATH = Path(__file__).parent / "data" / "budgets.yaml"

_METRIC_KEYS: dict[Literal["cost", "latency"], str] = {"cost": "cost", "latency": "latency"}


@dataclass
class Budget:
    scope: str  # e.g. "dining.hold_table" (per-tool) or "dining" (domain-level)
    metric: Literal["cost", "latency"]
    threshold: float


def load_budgets(path: Path | str | None = None) -> list[Budget]:
    """Read budget fixtures from `eval/data/budgets.yaml`. The exact
    threshold values are a planning-level parameter (spec.md Assumptions),
    not load-bearing for correctness — tune them without touching this
    function."""
    resolved = Path(path) if path is not None else _BUDGETS_PATH
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or []
    return [Budget(scope=entry["scope"], metric=entry["metric"], threshold=entry["threshold"]) for entry in raw]


def check_budgets(
    actuals: dict[str, dict[str, float]], budgets: list[Budget]
) -> list[BudgetFlag]:
    """Compare each `Budget`'s threshold against the matching actual (looked
    up by `scope` + `metric`, cost/latency both "lower is better" per
    `eval/gate.py`'s `_LOWER_IS_BETTER`). A scope with no recorded actual for
    a given budget is silently skipped — this module flags what it can
    observe, it does not treat missing measurement as a violation (that is
    a coverage concern, not a budget one)."""
    flags: list[BudgetFlag] = []
    for budget in budgets:
        scope_actuals = actuals.get(budget.scope)
        if scope_actuals is None:
            continue
        actual = scope_actuals.get(_METRIC_KEYS[budget.metric])
        if actual is None:
            continue
        if actual > budget.threshold:
            flags.append(
                BudgetFlag(
                    scope=budget.scope,
                    metric=budget.metric,
                    actual=actual,
                    threshold=budget.threshold,
                )
            )
    return flags
