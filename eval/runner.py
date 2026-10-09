"""T019 — `python -m eval.runner --tier pr|merge_queue|nightly` (FR-006/019):
the one command both CI and laptops call. This is the tier-DISPATCH
skeleton — it wires judge/coverage/baseline/gate/report together and
produces a valid, schema-conforming report end-to-end today (coverage +
binary-gate scope only); retrieval/trajectory/saga/cost-latency scope wiring
are added by US1 (T024/T025), US2, and US4 without needing to touch this
module's structure — each extends `_run_pr_tier`/`_run_merge_queue_tier`/
`_run_nightly_tier` below.

Exit-code mapping (`contracts/ci-tier-triggers-contract.md`) lives in `main()`
only — the report/verdict computation above it never varies by environment
(SC-006).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import yaml

from eval.baseline import (
    apply_judge_prompt_status,
    baseline_status_for,
    capture_baseline_from_run,
    is_eligible_baseline,
    load_baseline,
    save_baseline,
)
from eval.cost_latency import check_budgets, load_budgets
from eval.coverage import detect_coverage_requirements, evaluate_coverage, load_exemptions
from eval.gate import evaluate_statistical_metric, evaluate_strata
from eval.intent_scorer import (
    IntentExtractorUnavailableError,
    default_intent_extractor,
    score_intent_cases,
)
from eval.judge import (
    JudgeUnavailableError,
    build_default_judge_client,
    pinned_model_id,
    provider_name,
)
from eval.models import EvaluationRun, JudgeModelRecord, MetricScore
from eval.report import write_report
from eval.retrieval_scorer import score_retrieval_case

_RETRIEVAL_CASES_PATH = Path(__file__).parent / "data" / "retrieval_cases.yaml"
_INTENT_CASES_PATH = Path(__file__).parent / "data" / "intent_cases.yaml"
_T = TypeVar("_T")

# T024/T025/T056 — planning-level thresholds (spec.md Assumptions, same status as
# `eval/data/budgets.yaml`'s cost/latency numbers): tune freely without
# touching the gate-combinator logic in `eval/gate.py`. Metrics not listed
# here fall back to a conservative 0.5 floor.
_METRIC_FLOORS: dict[str, float] = {
    "context_recall": 0.7,
    "context_precision": 0.6,
    "faithfulness": 0.8,
    "answer_relevance": 0.7,
    "tool_selection": 0.8,
    "unnecessary_step_rate": 0.3,
    "plan_quality": 0.7,
    "intent_extraction_accuracy": 0.8,  # placeholder pending real traffic (spec.md Assumptions)
    "dietary_recall": 1.0,  # Principle IV: every stated restriction must survive to the model call
    # Feature 010 (contracts/routing-eval-gate.md) — placeholders pending calibration
    # (research.md R-15/R-16), computed on the `holdout` split only.
    "routing_accuracy": 0.95,
    "oos_passthrough_rate": 0.0,
    "substring_trap_misroutes": 0.0,
    "modify_book_starts": 0.0,
    "followup_accuracy": 0.95,
    "llm_tier_share": 0.20,  # report-only until calibrated (T070); see _report_only_metrics
    # Feature 011 (contracts/browse-eval-gate.md) — placeholders pending live calibration (T049), holdout only.
    "browse_accuracy": 0.95,
    "browse_catalogue_misroute": 0.0,
    "near_miss_browse_leak": 0.0,
    "stay_length_accuracy": 0.95,
    "browse_llm_tier_share": 0.10,  # report-only until calibrated (T049); see _report_only_metrics
}
_MARGIN_PCT = 0.10
_PR_TIER_RETRIEVAL_SAMPLE_SIZE = 5  # one case per domain, per FR-019's "stratified subset"

DIETARY_RECALL_BLOCKING = True
"""T053a (2026-09-27): calibrated clean against the live Foundry judge — 20 runs, sigma 0.0000,
zero cases ever missed a stated restriction (research.md R-16). One ambiguity the calibration
surfaced was fixed in the same change: an unqualified "X allergy" mention is genuinely ambiguous
between allergy_moderate and allergy_severe, and the real judge consistently (not flakily) resolves
it to allergy_severe, the safer default under Principle IV — `eval/data/intent_cases.yaml`'s two
affected cases were corrected rather than the model. `dietary_recall`'s 1.0 floor now blocks merges."""


ROUTING_LLM_TIER_SHARE_BLOCKING = True
"""Feature 010 (research R-15/T070, flipped T080 2026-09-28): calibrated against real Azure
OpenAI tier-2 traffic over the holdout split (68 cases, after moving 2 `requires_tier2` cases
from `dev` into `holdout` — every prior holdout case was tier-0/1-only, which would have made
this metric vacuously 0.0 forever, not a real gate). Measured `llm_tier_share = 0.029` (2/68),
comfortably under the 0.20 ceiling; recorded in research.md R-15. Mirrors
`DIETARY_RECALL_BLOCKING`'s own report-only-until-calibrated pattern above."""


BROWSE_LLM_TIER_SHARE_BLOCKING = False
"""Feature 011 (T048/T049): report-only until measured against the live tier-2 classifier (research R-13)."""


def _report_only_metrics() -> frozenset[str]:
    metrics = set() if DIETARY_RECALL_BLOCKING else {"dietary_recall"}
    if not ROUTING_LLM_TIER_SHARE_BLOCKING:
        metrics.add("llm_tier_share")
    if not BROWSE_LLM_TIER_SHARE_BLOCKING:
        metrics.add("browse_llm_tier_share")
    return frozenset(metrics)

# T027 — a fixed, representative actuals set standing in for real trace-backend
# measurements (no live OTLP query API exists in this build, per
# `eval/conversation_record.py`'s disclosed `SpanLookup` gap). Every value here
# is comfortably inside `eval/data/budgets.yaml`'s thresholds — this exercises
# the wiring (FR-004/FR-005's per-tier "fixed cost/latency trace set"), not a
# real regression signal; US4's live measurement path replaces this dict.
_FIXED_COST_LATENCY_ACTUALS: dict[str, dict[str, float]] = {
    "dining.hold_table": {"cost": 0.004, "latency": 850.0},
    "dining": {"cost": 0.03, "latency": 1800.0},
    "gaming.read_balance": {"latency": 400.0},
}


def _retrieval_cases(sample_size: int | None = None) -> list[dict[str, Any]]:
    cases = yaml.safe_load(_RETRIEVAL_CASES_PATH.read_text(encoding="utf-8")) or []
    if sample_size is None:
        return cases
    return _stratify_by_tenant(cases, sample_size)


def _stratify_by_tenant(cases: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    """T056: round-robins across tenants (in a fixed, deterministic order) so the PR tier's
    sampled subset always has cases from every tenant present in the data, not just whichever
    tenant happened to sort first (FR-021a's "each tenant has >= 2 cases" for the stratified
    sample)."""
    by_tenant: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        by_tenant.setdefault(case.get("tenant_id", ""), []).append(case)
    tenants = sorted(by_tenant)
    result: list[dict[str, Any]] = []
    while len(result) < sample_size and any(by_tenant[t] for t in tenants):
        for tenant in tenants:
            if len(result) >= sample_size:
                break
            if by_tenant[tenant]:
                result.append(by_tenant[tenant].pop(0))
    return result


def _intent_cases() -> tuple[Any, list[dict[str, Any]]]:
    data = yaml.safe_load(_INTENT_CASES_PATH.read_text(encoding="utf-8")) or {}
    from datetime import date as _date

    today = data.get("today")
    if isinstance(today, str):
        today = _date.fromisoformat(today)
    return today, data.get("cases", [])


def _floor_for(metric_name: str) -> float:
    return _METRIC_FLOORS.get(metric_name, 0.5)


def _is_deliberate_fault_response(exc: BaseException) -> bool:
    """FR-021's fault-injection-vs-genuine-outage discriminator
    (`sims/_shared/faults.py`): a deliberately injected `X-Sim-Fault` fault
    still returns a well-formed HTTP response with the fault middleware's
    `{"error": {"code": ...}}` envelope — that's a real, scoreable test
    outcome, not a missing dependency. Genuine unreachability (connection
    refused/reset, DNS failure, timeout with no response object at all)
    carries no such envelope. Only an exception with a `.response` we can
    parse as that envelope counts as deliberate; anything else, including a
    response we can't parse, is treated as a genuine outage."""
    response = getattr(exc, "response", None)
    if response is None:
        return False
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 — unparseable body is not a recognizable envelope
        return False
    return isinstance(body, dict) and isinstance(body.get("error"), dict) and "code" in body["error"]


def _is_transport_error(exc: BaseException) -> bool:
    """Connection-level failures from `requests`/`httpx`, checked by class
    name rather than a hard import — this module must not require either
    package to be installed to run the (currently sim-call-free) PR tier."""
    module = type(exc).__module__
    name = type(exc).__name__
    if "requests" in module and "ConnectionError" in name:
        return True
    return bool("httpx" in module and name in ("ConnectError", "ConnectTimeout", "TransportError"))


def _run_with_retry(
    fn: Callable[[], _T], *, attempts: int = 3, backoff_seconds: float = 0.05
) -> _T:
    """FR-021-024/T026: up to `attempts` tries (the initial call plus 2
    retries) with a small linear backoff, total added time bounded well
    within the PR tier's budget (attempts=3, backoff<=0.15s total). Raises
    `JudgeUnavailableError` (or the last transport error) after the final
    attempt so the caller can mark the case `NOT_EVALUATED` rather than
    silently passing or crashing the whole run."""
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except JudgeUnavailableError as exc:
            last_exc = exc
        except Exception as exc:
            if _is_deliberate_fault_response(exc):
                raise  # a real (injected) failure — score it, don't retry-and-hide it
            if not _is_transport_error(exc):
                raise
            last_exc = exc
        if attempt < attempts - 1:
            time.sleep(backoff_seconds * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _score_retrieval_subset(
    run: EvaluationRun, *, cases: list[dict[str, Any]], repeats: int
) -> set[str]:
    """Scores each retrieval case (T024's PR-tier stratified subset or
    T025's merge-queue full set at `repeats=3`), applying the statistical
    gate to every resulting `MetricScore` and appending it to
    `run.metric_scores`. A case whose judge call never succeeds after retry
    is recorded in `run.not_evaluated_cases` (-> `INCOMPLETE`, T026) instead
    of silently dropped. Returns the set of domains actually scored, for
    `_apply_coverage`'s `covered_surface_ids`."""
    judge_client = build_default_judge_client()
    baseline = load_baseline()
    covered_domains: set[str] = set()
    baseline_status = baseline_status_for(run.judge_model, baseline)

    def _score_one(case: dict[str, Any]) -> tuple[dict[str, Any], list[MetricScore] | BaseException]:
        def _call() -> list[MetricScore]:
            return score_retrieval_case(case, judge_client=judge_client, repeats=repeats)

        try:
            return case, _run_with_retry(_call)
        except (JudgeUnavailableError, Exception) as exc:  # noqa: BLE001 — classified by the caller
            return case, exc

    # T027: each case's judge calls are independent of every other case, so
    # they run concurrently rather than one-at-a-time — the retrieval subset
    # (5 cases PR-tier, more at merge-queue) is exactly the "batched
    # judge-model calls" FR-020 asks for. Thread pool, not asyncio, since
    # `JudgeClient.complete` is a synchronous call (`eval/judge.py`) and the
    # concurrency here is I/O-bound (network-bound judge calls), not
    # CPU-bound — no real benchmark exists in this sandbox (no live judge
    # endpoint), so this is a structural optimization, documented rather
    # than measured.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(cases)))) as pool:
        results = list(pool.map(_score_one, cases))

    for case, outcome in results:
        if isinstance(outcome, JudgeUnavailableError):
            run.not_evaluated_cases.append(case["id"])
            continue
        if isinstance(outcome, BaseException):
            if _is_transport_error(outcome):
                run.not_evaluated_cases.append(case["id"])
                continue
            raise outcome
        for score in outcome:
            baseline_mean = (
                baseline.per_metric_mean.get(score.metric_name) if baseline is not None else None
            )
            baseline_stdev = (
                baseline.per_metric_stdev.get(score.metric_name, 0.0) if baseline is not None else 0.0
            )
            evaluate_statistical_metric(
                score,
                floor=_floor_for(score.metric_name),
                baseline_mean=baseline_mean,
                baseline_stdev=baseline_stdev,
                margin_pct=_MARGIN_PCT,
                baseline_status=baseline_status,
            )
            run.metric_scores.append(score)
        covered_domains.add(case.get("domain", ""))
    return covered_domains


def _score_intent(run: EvaluationRun) -> None:
    """T053/T056: runs `eval.intent_scorer` (deterministic, no judge) over `intent_cases.yaml`.
    No Azure credentials configured -> the whole set is marked `NOT_EVALUATED` (INCOMPLETE), never
    a fabricated pass, mirroring the judge's own `JudgeUnavailableError` handling."""
    today, cases = _intent_cases()
    if not cases:
        return
    try:
        extractor = default_intent_extractor()
    except IntentExtractorUnavailableError:
        run.not_evaluated_cases.append("dining_intent_cases")
        return
    run.metric_scores.extend(score_intent_cases(cases, extractor=extractor, today=today))


def _score_routing(run: EvaluationRun) -> None:
    """Feature 010, T069 (US5): runs `eval.routing_scorer` (deterministic
    tiers 0/1, no judge for those; tier-2 cases need Azure credentials)
    over `eval/data/routing_cases.yaml`'s `holdout` split — the only split
    blocking metrics are computed from (analyze finding D1). `requires_tier2`
    cases without credentials are marked NOT_EVALUATED, never a fabricated
    pass."""
    import asyncio

    from eval.routing_scorer import load_routing_cases, score_routing_cases

    cases = load_routing_cases()
    if not cases:
        return
    scores, not_evaluated = asyncio.run(score_routing_cases(cases, split="holdout"))

    baseline = load_baseline()
    baseline_status = baseline_status_for(run.judge_model, baseline)
    for score in scores:
        baseline_mean = baseline.per_metric_mean.get(score.metric_name) if baseline is not None else None
        baseline_stdev = baseline.per_metric_stdev.get(score.metric_name, 0.0) if baseline is not None else 0.0
        evaluate_statistical_metric(
            score,
            floor=_floor_for(score.metric_name),
            baseline_mean=baseline_mean,
            baseline_stdev=baseline_stdev,
            margin_pct=_MARGIN_PCT,
            baseline_status=baseline_status,
        )
    run.metric_scores.extend(scores)
    if not_evaluated:
        run.not_evaluated_cases.append("routing_tier2_cases")


def _score_saga(run: EvaluationRun, *, domains: list[str] | None) -> None:
    """T024 (touched-domain scope) / T025 (full sweep, `domains=None`).
    Delegates to US2's `eval.saga_suite.run_saga_suite` (T031), which landed
    with exactly the contract this call site was written against:
    `def run_saga_suite(domains: list[str] | None = None) -> list[SagaCaseResult]`."""
    from eval.saga_suite import run_saga_suite

    run.saga_case_results.extend(run_saga_suite(domains=domains))


def _apply_cost_latency(run: EvaluationRun) -> None:
    """T024/T025's "fixed representative cost/latency trace set" — see
    `_FIXED_COST_LATENCY_ACTUALS`'s docstring for why this is a stand-in,
    not a live measurement."""
    run.budget_flags = check_budgets(_FIXED_COST_LATENCY_ACTUALS, load_budgets())


def _git_ref() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001 — not fatal; a missing git ref shouldn't crash the gate
        return "unknown"


def _changed_files() -> list[str]:
    """Diffs the current branch against `origin/main` when available;
    returns an empty list (no coverage requirements detected) otherwise —
    the PR/merge-queue workflows always have a real base to diff against
    (`contracts/ci-tier-triggers-contract.md`); a bare local run without one
    degrades to "nothing new to cover" rather than crashing."""
    try:
        output = subprocess.check_output(
            ["git", "diff", "--name-only", "origin/main...HEAD"], stderr=subprocess.DEVNULL
        )
        return [line for line in output.decode().splitlines() if line]
    except Exception:  # noqa: BLE001
        return []


def _touched_domains(changed_files: list[str]) -> set[str]:
    """T024's "domains the change's coverage requirement touches" —
    derived from `agents/<domain>/...` path prefixes in the diff, the same
    file-path-heuristic spirit `eval/coverage.py` already uses for its own
    requirement detection (ADR-040)."""
    domains: set[str] = set()
    for path in changed_files:
        parts = path.replace("\\", "/").split("/")
        if len(parts) >= 2 and parts[0] == "agents":
            domains.add(parts[1])
    return domains


def _new_run(tier: str) -> EvaluationRun:
    judge_model = JudgeModelRecord(provider=provider_name(), model_id=pinned_model_id())
    return EvaluationRun(
        run_id=f"{tier}-{uuid.uuid4().hex[:12]}",
        tier=tier,  # type: ignore[arg-type]
        code_ref=_git_ref(),
        judge_model=judge_model,
    )


def _apply_coverage(run: EvaluationRun, *, covered_surface_ids: set[str] | None = None) -> None:
    """FR-009a: every tier checks coverage requirements against what the
    change touches. `covered_surface_ids` is populated by US1/US2's scoring
    wiring once a change's coverage is actually exercised — empty here
    means "nothing yet proven covered," which correctly fails closed."""
    changed = _changed_files()
    requirements = detect_coverage_requirements(changed)
    exemptions = load_exemptions()
    rows, active = evaluate_coverage(requirements, covered_surface_ids or set(), exemptions)
    run.coverage = rows
    run.active_exemptions = [
        {
            "exemption_id": e.exemption_id,
            "surface_id": e.surface_id,
            "reason": e.reason,
            "tracking_issue": e.tracking_issue,
        }
        for e in active
    ]


def _apply_baseline_status(run: EvaluationRun) -> None:
    baseline = load_baseline()
    run.baseline_status = baseline_status_for(run.judge_model, baseline)


def _apply_gate(run: EvaluationRun) -> None:
    """T052/T056: the per-`(metric, tenant)` gate (T042) and the judge-prompt staleness check
    (T041/T051), run last so every scorer above has had a chance to append to `run.metric_scores`."""
    from prompts.lock import load_state

    baseline = load_baseline()
    try:
        lock_state = load_state()
    except Exception:  # noqa: BLE001 — a broken lock file must not crash the whole eval run
        lock_state = None
    apply_judge_prompt_status(run, baseline, lock_state)
    evaluate_strata(
        run,
        floors=_METRIC_FLOORS,
        baseline=baseline,
        margin_pct=_MARGIN_PCT,
        report_only=_report_only_metrics(),
    )


def _run_pr_tier(run: EvaluationRun) -> None:
    """FR-019 PR tier: binary gates + stratified retrieval + touched-domain
    saga + fixed cost/latency trace. Scoring wiring: T024 (US1).

    Trajectory scoring (tool-selection/unnecessary-step-rate) is exercised
    by each domain's own `make eval` run (T022/T023's `attach_trajectory_scores`
    wiring in `agents/*/eval/runner.py`), not re-run here — this tier scores
    the cross-cutting concerns (retrieval quality, saga permutations,
    cost/latency) that no single domain runner already covers.
    """
    changed = _changed_files()
    touched_domains = _touched_domains(changed)
    covered = _score_retrieval_subset(
        run, cases=_retrieval_cases(_PR_TIER_RETRIEVAL_SAMPLE_SIZE), repeats=1
    )
    _score_saga(run, domains=sorted(touched_domains) or None)
    _score_intent(run)
    _score_routing(run)
    _apply_cost_latency(run)
    _apply_coverage(run, covered_surface_ids=covered)
    _apply_baseline_status(run)
    _apply_gate(run)


def _run_merge_queue_tier(run: EvaluationRun) -> None:
    """FR-019 merge-queue tier: full retrieval (3x) + full trajectory + full
    saga sweep + full adversarial script. Scoring wiring: T025 (US1),
    T031-034 (US2).

    The full saga sweep (`domains=None`) depends on US2's `eval/saga_suite.py`
    (T031) — see `_score_saga`'s docstring for the exact pending contract and
    what changes once it lands."""
    covered = _score_retrieval_subset(run, cases=_retrieval_cases(), repeats=3)
    _score_saga(run, domains=None)
    _score_intent(run)
    _score_routing(run)
    _apply_cost_latency(run)
    _apply_coverage(run, covered_surface_ids=covered)
    _apply_baseline_status(run)
    _apply_gate(run)


def _run_nightly_tier(run: EvaluationRun) -> None:
    """FR-019 nightly tier: never blocks. Baseline-drift reporting (T043,
    `eval/drift.py`) surfaces findings as coverage-style informational rows
    rather than metric_scores — a drift finding is a trend signal, not a
    per-run pass/fail, and must never influence `compute_verdict` (the
    nightly tier's own verdict is informational only; nothing reads it as a
    gate). Red-team/adversarial-style probing (FR-019's other nightly
    bullet) is intentionally not built here — it maps to FR-016's optional,
    non-blocking platform add-ons (e.g. a cloud platform's built-in safety
    evaluators), not required infrastructure this feature must build from
    scratch."""
    from eval.drift import detect_drift

    findings = detect_drift()
    run.drift_findings = [
        {
            "metric_name": f.metric_name,
            "domain": f.domain,
            "oldest_mean": f.oldest_mean,
            "newest_mean": f.newest_mean,
            "delta": f.delta,
            "run_count": f.run_count,
        }
        for f in findings
    ]
    # Deliberately does NOT touch run.coverage/metric_scores/saga_case_results
    # — a drift finding must never influence compute_verdict (FR-019: the
    # nightly tier never blocks a merge, and its own exit code must reflect
    # that regardless of what it found).
    _apply_baseline_status(run)


_TIER_RUNNERS = {
    "pr": _run_pr_tier,
    "merge_queue": _run_merge_queue_tier,
    "nightly": _run_nightly_tier,
}


def run(tier: str) -> EvaluationRun:
    from prompts.registry import validate_all

    validate_all(import_call_sites=True)  # FR-010: an invalid/revoked Git template must never ship
    run_obj = _new_run(tier)
    _TIER_RUNNERS[tier](run_obj)
    run_obj.completed_at = datetime.now(UTC)
    return run_obj


def _check_baseline_prompts() -> int:
    """`--check-baseline-prompts` (T058's rebaseline job): exits 1 if any judge prompt id the
    baseline recorded now has a different hash in Git, without running a full eval. Used to decide
    whether a rebaseline is actually needed before paying for a full merge-queue-tier run."""
    from prompts.canonical import template_sha256
    from prompts.schema import TEMPLATES_DIR, load_all_templates

    baseline = load_baseline()
    if baseline is None or not baseline.judge_prompt_hashes:
        print("baseline: judge prompt hashes absent", file=sys.stderr)
        return 1
    templates = load_all_templates(TEMPLATES_DIR)
    stale = sorted(
        prompt_id
        for prompt_id, recorded in baseline.judge_prompt_hashes.items()
        if prompt_id in templates and template_sha256(templates[prompt_id]) != recorded
    )
    if stale:
        print(f"BASELINE_STALE: {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


def _maybe_update_baseline(run_obj: EvaluationRun) -> None:
    """T029/FR-007c/ADR-045: only the merge-queue tier, and only on a
    COMPLETE verdict (PASS or FAIL — never INCOMPLETE), updates
    `eval/data/baseline.json`. `contracts/ci-tier-triggers-contract.md`
    additionally scopes this to a run against `main` — enforced by the
    calling workflow (`merge-queue-gate.yml`) only invoking this tier on the
    `merge_group` event, not by this function checking the branch itself."""
    if is_eligible_baseline(run_obj):
        save_baseline(capture_baseline_from_run(run_obj))
        print(f"[{run_obj.tier}] baseline updated from {run_obj.run_id}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.runner")
    parser.add_argument("--tier", choices=["pr", "merge_queue", "nightly"], required=True)
    parser.add_argument(
        "--check-baseline-prompts", action="store_true",
        help="exit 1 if a baseline-recorded judge prompt hash no longer matches Git; no eval run",
    )
    args = parser.parse_args(argv)

    if args.check_baseline_prompts:
        return _check_baseline_prompts()

    run_obj = run(args.tier)
    path = write_report(run_obj)
    print(f"[{run_obj.tier}] verdict={run_obj.verdict} report={path}", file=sys.stderr)
    if run_obj.judge_prompt_status == "BASELINE_STALE":
        names = ", ".join(run_obj.stale_judge_prompts)
        print(
            f"[{run_obj.tier}] BASELINE_STALE: {names} — add the `rebaseline` label to this PR",
            file=sys.stderr,
        )
    _maybe_update_baseline(run_obj)

    if run_obj.verdict == "PASS":
        return 0
    if run_obj.verdict == "INCOMPLETE":
        import os

        blocks = os.environ.get("EVAL_INCOMPLETE_BLOCKS", "false").lower() == "true"
        return 1 if blocks else 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
