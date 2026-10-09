"""T042 — unit tests for `eval/cost_latency.py` (FR-004/FR-005, US4)."""

from __future__ import annotations

from pathlib import Path

from eval.cost_latency import Budget, check_budgets, load_budgets
from eval.models import BudgetFlag

_BUDGETS = [
    Budget(scope="dining.hold_table", metric="cost", threshold=0.02),
    Budget(scope="dining.hold_table", metric="latency", threshold=2000),
]


def test_actual_exceeding_budget_produces_a_flag_naming_that_scope():
    actuals = {"dining.hold_table": {"cost": 0.05, "latency": 900.0}}
    flags = check_budgets(actuals, _BUDGETS)
    assert len(flags) == 1
    flag = flags[0]
    assert isinstance(flag, BudgetFlag)
    assert flag.scope == "dining.hold_table"
    assert flag.metric == "cost"
    assert flag.actual == 0.05
    assert flag.threshold == 0.02


def test_compliant_conversation_produces_no_flags():
    actuals = {"dining.hold_table": {"cost": 0.01, "latency": 500.0}}
    assert check_budgets(actuals, _BUDGETS) == []


def test_missing_actual_for_a_scope_is_skipped_not_flagged():
    assert check_budgets({}, _BUDGETS) == []


def test_multiple_metrics_over_budget_each_produce_their_own_flag():
    actuals = {"dining.hold_table": {"cost": 0.05, "latency": 3000.0}}
    flags = check_budgets(actuals, _BUDGETS)
    assert {f.metric for f in flags} == {"cost", "latency"}


def test_load_budgets_reads_fixture_file():
    budgets = load_budgets()
    assert budgets, "expected eval/data/budgets.yaml to contain at least one budget"
    assert all(isinstance(b, Budget) for b in budgets)
    scopes = {(b.scope, b.metric) for b in budgets}
    assert ("dining.hold_table", "cost") in scopes
    assert ("dining.hold_table", "latency") in scopes


def test_load_budgets_reads_an_explicit_path(tmp_path: Path):
    custom = tmp_path / "custom_budgets.yaml"
    custom.write_text(
        "- scope: gaming.read_balance\n  metric: latency\n  threshold: 999\n",
        encoding="utf-8",
    )
    budgets = load_budgets(custom)
    assert budgets == [Budget(scope="gaming.read_balance", metric="latency", threshold=999)]
