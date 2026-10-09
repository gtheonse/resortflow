"""T057 — eval runner: executes every case in `test_cases.yaml` against the
compiled dining subgraph + `FakeDiningTools`, asserts the declared
`expect` outcomes, and writes a machine-readable summary to
`results.json`.

Case schema (see test_cases.yaml for the full 25):

    id: str                      # unique case id
    category: str                # happy_path | dietary_conflict |
                                  # availability_failure | resume_interrupt |
                                  # edge_case
    tenant_id: MGV | MHB
    text: str                    # guest's free-text request
    fixture: {..}                # optional FakeDiningTools kwargs; the
                                  # fail_* sub-keys are {vendor_code,
                                  # http_status} dicts, turned into
                                  # DiningToolError instances
    resume: {approved: bool}     # optional — if present, the case invokes
                                  # once (expecting an interrupt), then
                                  # resumes with Command(resume=...)
    expect: {..}                 # assertions — see `_check_expectations`

Run directly (`python -m agents.dining.eval.runner`) or via
`agents/dining/eval/test_eval_set.py` (the pytest entry point `make eval`
exercises)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.graph import compile_dining_graph, new_session_state
from agents.dining.nodes.intent_parser import RuleBasedIntentExtractor
from agents.dining.serde import plain_serde
from agents.dining.tests.fakes import FakeDiningTools
from agents.dining.tools import DiningToolError
from eval.trajectory_scorer import attach_trajectory_scores

CASES_PATH = Path(__file__).parent / "test_cases.yaml"
RESULTS_PATH = Path(__file__).parent / "results.json"
FIXED_TODAY = date(2026, 9, 11)


class _FixedTodayExtractor(RuleBasedIntentExtractor):
    """Rule-based extractor pinned to a fixed `today` — "tonight"/"tomorrow"
    must resolve deterministically across eval runs."""

    def extract(self, text: str, *, today: date) -> Any:
        return super().extract(text, today=FIXED_TODAY)


def _build_tools(case: dict) -> FakeDiningTools:
    fixture = dict(case.get("fixture") or {})
    for key in ("fail_availability", "fail_hold", "fail_confirm", "fail_waitlist"):
        if key in fixture and fixture[key] is not None:
            spec = fixture[key]
            fixture[key] = DiningToolError(
                f"injected failure: {spec.get('vendor_code')}",
                vendor_code=spec.get("vendor_code"),
                http_status=spec.get("http_status"),
            )
    fixture.setdefault("tenant_id", case["tenant_id"])
    return FakeDiningTools(**fixture)


def _check_expectations(case: dict, result: dict) -> list[str]:
    """Return a list of failure reasons — empty means the case passed."""
    expect = case.get("expect") or {}
    problems: list[str] = []

    if "confirmed" in expect:
        actual = len(result.get("confirmations", [])) > 0
        if actual != expect["confirmed"]:
            problems.append(f"confirmed: expected {expect['confirmed']}, got {actual}")

    if "waitlisted" in expect:
        actual = bool(result.get("waitlisted"))
        if actual != expect["waitlisted"]:
            problems.append(f"waitlisted: expected {expect['waitlisted']}, got {actual}")

    if "awaiting_approval" in expect:
        actual = bool(result.get("awaiting_approval"))
        if actual != expect["awaiting_approval"]:
            problems.append(f"awaiting_approval: expected {expect['awaiting_approval']}, got {actual}")

    if "dietary_conflict" in expect:
        actual = len(result.get("dietary_conflicts", []) or []) > 0
        if actual != expect["dietary_conflict"]:
            problems.append(f"dietary_conflict: expected {expect['dietary_conflict']}, got {actual}")

    if "failure_error_class" in expect:
        failures = result.get("failures", [])
        actual = failures[-1].error_class.value if failures else None
        if actual != expect["failure_error_class"]:
            problems.append(
                f"failure_error_class: expected {expect['failure_error_class']}, got {actual}"
            )

    if "hold_released" in expect:
        actual = any(h.status.value == "released" for h in result.get("holds", []))
        if actual != expect["hold_released"]:
            problems.append(f"hold_released: expected {expect['hold_released']}, got {actual}")

    if "zero_matches" in expect:
        actual = len(result.get("proposals", [])) == 0
        if actual != expect["zero_matches"]:
            problems.append(f"zero_matches: expected {expect['zero_matches']}, got {actual}")

    if "ambiguous" in expect:
        intent = result.get("intent") or {}
        actual = len(intent.get("ambiguous_fields", []) or []) > 0
        if actual != expect["ambiguous"]:
            problems.append(f"ambiguous: expected {expect['ambiguous']}, got {actual}")

    if "dietary_captured" in expect:
        intent = result.get("intent") or {}
        actual = len(intent.get("dietary", []) or []) > 0
        if actual != expect["dietary_captured"]:
            problems.append(f"dietary_captured: expected {expect['dietary_captured']}, got {actual}")

    return problems


async def run_case(case: dict) -> dict:
    tools = _build_tools(case)
    checkpointer = MemorySaver(serde=plain_serde())
    app = compile_dining_graph(
        tools, checkpointer=checkpointer, intent_extractor=_FixedTodayExtractor(), trace=False
    )
    state = new_session_state(
        tenant_id=case["tenant_id"], guest_id="eval-guest", text=case["text"], session_id=case["id"]
    )
    config = {"configurable": {"thread_id": case["id"]}}

    try:
        result = await app.ainvoke(state, config=config)
        if case.get("resume"):
            result = await app.ainvoke(Command(resume=case["resume"]), config=config)
        problems = _check_expectations(case, result)
        outcome = {"id": case["id"], "category": case["category"], "passed": not problems, "problems": problems}
        attach_trajectory_scores(case, result, outcome, domain="dining")
        return outcome
    except Exception as exc:  # noqa: BLE001 — a case erroring out is a failed case, not a runner crash
        return {
            "id": case["id"],
            "category": case["category"],
            "passed": False,
            "problems": [f"exception: {type(exc).__name__}: {exc}"],
        }


async def run_all(cases_path: Path = CASES_PATH) -> dict:
    cases = yaml.safe_load(cases_path.read_text())
    outcomes = [await run_case(case) for case in cases]
    passed = sum(1 for o in outcomes if o["passed"])
    summary = {
        "total": len(outcomes),
        "passed": passed,
        "failed": len(outcomes) - passed,
        "cases": outcomes,
    }
    return summary


def write_results(summary: dict, path: Path = RESULTS_PATH) -> None:
    path.write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    result = asyncio.run(run_all())
    write_results(result)
    print(f"{result['passed']}/{result['total']} eval cases passed")
    for case in result["cases"]:
        if not case["passed"]:
            print(f"  FAIL {case['id']} ({case['category']}): {case['problems']}")
