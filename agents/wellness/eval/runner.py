"""T060 — eval runner for wellness, generalizing
`agents/dining/eval/runner.py`'s exact `run_case`/`run_all`/`write_results`
shape. Drives the compiled wellness subgraph against `FakeWellnessTools` —
no live sims/gateway required."""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.serde import plain_serde
from agents.wellness.graph import compile_wellness_graph
from agents.wellness.nodes.intent_parser import RuleBasedIntentExtractor
from agents.wellness.state import WellnessState
from agents.wellness.tests.fakes import FakeWellnessTools
from agents.wellness.tools import WellnessToolError
from eval.trajectory_scorer import attach_trajectory_scores

CASES_PATH = Path(__file__).parent / "test_cases.yaml"
RESULTS_PATH = Path(__file__).parent / "results.json"
FIXED_TODAY = date(2026, 9, 11)


class _FixedTodayExtractor(RuleBasedIntentExtractor):
    def extract(self, text: str, *, today: date) -> Any:
        return super().extract(text, today=FIXED_TODAY)


def _new_session_state(*, tenant_id: str, guest_id: str, session_id: str, text: str) -> WellnessState:
    return WellnessState(
        tenant_id=tenant_id, guest_id=guest_id, session_id=session_id,
        jurisdiction="NV" if tenant_id == "MGV" else "MA", entitlements=[],
        saga_id=uuid.uuid4().hex, messages=[HumanMessage(content=text)],
        plan=None, replan_count=0, proposals=[], holds=[], confirmations=[],
        failures=[], retrieved=[], citations=[], awaiting_approval=False,
        approval_scope=None, policy_flags=[], escalate=False, saga_log=[],
        graph_version="5.0", clarification_count=0, escalate_reason=None,
    )


def _build_tools(case: dict) -> FakeWellnessTools:
    fixture = dict(case.get("fixture") or {})
    for key in ("fail_availability", "fail_hold", "fail_confirm"):
        if key in fixture and fixture[key] is not None:
            spec = fixture[key]
            fixture[key] = WellnessToolError(
                f"injected failure: {spec.get('vendor_code')}",
                vendor_code=spec.get("vendor_code"), http_status=spec.get("http_status"),
            )
    fixture.setdefault("tenant_id", case["tenant_id"])
    return FakeWellnessTools(**fixture)


def _check_expectations(case: dict, result: dict) -> list[str]:
    expect = case.get("expect") or {}
    problems: list[str] = []

    if "confirmed" in expect:
        actual = len(result.get("confirmations", [])) > 0
        if actual != expect["confirmed"]:
            problems.append(f"confirmed: expected {expect['confirmed']}, got {actual}")

    if "awaiting_approval" in expect:
        actual = bool(result.get("awaiting_approval"))
        if actual != expect["awaiting_approval"]:
            problems.append(f"awaiting_approval: expected {expect['awaiting_approval']}, got {actual}")

    if "interrupted" in expect:
        actual = bool(result.get("__interrupt__"))
        if actual != expect["interrupted"]:
            problems.append(f"interrupted: expected {expect['interrupted']}, got {actual}")

    if "failure_error_class" in expect:
        failures = result.get("failures", [])
        actual = failures[-1].error_class.value if failures else None
        if actual != expect["failure_error_class"]:
            problems.append(f"failure_error_class: expected {expect['failure_error_class']}, got {actual}")

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

    return problems


async def run_case(case: dict) -> dict:
    tools = _build_tools(case)
    checkpointer = MemorySaver(serde=plain_serde())
    app = compile_wellness_graph(
        tools, checkpointer=checkpointer, intent_extractor=_FixedTodayExtractor(), trace=False
    )
    state = _new_session_state(tenant_id=case["tenant_id"], guest_id="eval-guest", session_id=case["id"], text=case["text"])
    config = {"configurable": {"thread_id": case["id"]}}

    try:
        result = await app.ainvoke(state, config=config)
        if case.get("resume"):
            result = await app.ainvoke(Command(resume=case["resume"]), config=config)
        problems = _check_expectations(case, result)
        outcome = {"id": case["id"], "category": case["category"], "passed": not problems, "problems": problems}
        attach_trajectory_scores(case, result, outcome, domain="wellness")
        return outcome
    except Exception as exc:  # noqa: BLE001 — a case erroring out is a failed case, not a runner crash
        return {
            "id": case["id"], "category": case["category"], "passed": False,
            "problems": [f"exception: {type(exc).__name__}: {exc}"],
        }


async def run_all(cases_path: Path = CASES_PATH) -> dict:
    cases = yaml.safe_load(cases_path.read_text())
    outcomes = [await run_case(case) for case in cases]
    passed = sum(1 for o in outcomes if o["passed"])
    return {"total": len(outcomes), "passed": passed, "failed": len(outcomes) - passed, "cases": outcomes}


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
