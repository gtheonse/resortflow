"""T016/T048/T051 — Baseline load/store/eligibility, `BASELINE_INVALID` and `BASELINE_STALE`
handling (FR-007c, FR-016a/FR-021, ADR-042, ADR-045, ADR-054, research R-10). Stored as a
versioned repo file (`eval/data/baseline.json`) per ADR-045 — updated only by the merge-queue
tier's automated commit on `main` (FR-007c, `eval/runner.py`) or the label-triggered rebaseline
job on a PR branch (ADR-054)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from eval.models import Baseline, BaselineStatus, EvaluationRun, JudgeModelRecord, JudgePromptStatus
from prompts.lock import LockState

_BASELINE_PATH = Path(__file__).parent / "data" / "baseline.json"


def load_baseline(path: Path = _BASELINE_PATH) -> Baseline | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    if raw is None:
        return None
    return Baseline(
        source_run_id=raw["source_run_id"],
        judge_model=JudgeModelRecord(**raw["judge_model"]),
        per_metric_mean=raw["per_metric_mean"],
        per_metric_stdev=raw["per_metric_stdev"],
        judge_prompt_hashes=raw.get("judge_prompt_hashes", {}),
        per_tenant_mean=raw.get("per_tenant_mean", {}),
        per_tenant_stdev=raw.get("per_tenant_stdev", {}),
        captured_at=datetime.fromisoformat(raw["captured_at"]),
    )


def save_baseline(baseline: Baseline, path: Path = _BASELINE_PATH) -> None:
    payload = asdict(baseline)
    payload["captured_at"] = baseline.captured_at.isoformat()
    path.write_text(json.dumps(payload, indent=2))


def is_eligible_baseline(run: EvaluationRun) -> bool:
    """FR-007c: only a merge-queue-tier run that reached a COMPLETE verdict
    (`PASS` or `FAIL`, never `INCOMPLETE`) may become the baseline."""
    return run.tier == "merge_queue" and run.verdict in ("PASS", "FAIL")


def capture_baseline_from_run(run: EvaluationRun) -> Baseline:
    """Builds a `Baseline` from an eligible run's statistical-gate metric
    scores, plus (T048) the judge-prompt hashes it used and per-tenant stats. Caller MUST check
    `is_eligible_baseline(run)` first (FR-007c) — this function does not re-check, so a caller
    cannot accidentally rely on it to enforce eligibility."""
    per_metric_mean: dict[str, float] = {}
    per_metric_stdev: dict[str, float] = {}
    judge_prompt_hashes: dict[str, str] = {}
    per_tenant_values: dict[str, dict[str, list[float]]] = {}
    for score in run.metric_scores:
        if score.gate_type != "statistical":
            continue
        per_metric_mean[score.metric_name] = score.mean
        per_metric_stdev[score.metric_name] = score.stdev
        if score.judge_model.prompt_id and score.judge_model.prompt_sha256:
            judge_prompt_hashes[score.judge_model.prompt_id] = score.judge_model.prompt_sha256
        if score.tenant_id is not None:
            per_tenant_values.setdefault(score.metric_name, {}).setdefault(score.tenant_id, []).extend(
                score.raw_values
            )
    import statistics as _statistics

    per_tenant_mean = {
        metric: {tenant: _statistics.fmean(values) for tenant, values in by_tenant.items()}
        for metric, by_tenant in per_tenant_values.items()
    }
    per_tenant_stdev = {
        metric: {
            tenant: (_statistics.stdev(values) if len(values) > 1 else 0.0) for tenant, values in by_tenant.items()
        }
        for metric, by_tenant in per_tenant_values.items()
    }
    return Baseline(
        source_run_id=run.run_id,
        judge_model=run.judge_model,
        per_metric_mean=per_metric_mean,
        per_metric_stdev=per_metric_stdev,
        judge_prompt_hashes=judge_prompt_hashes,
        per_tenant_mean=per_tenant_mean,
        per_tenant_stdev=per_tenant_stdev,
        captured_at=datetime.now(UTC),
    )


def stale_judge_prompts_for(
    run: EvaluationRun, baseline: Baseline | None, lock_state: LockState | None = None
) -> list[str]:
    """T051/FR-016a: prompt ids the run actually used (`MetricScore.judge_model.prompt_id`) whose
    CURRENT hash differs from what the baseline recorded for that id, or whose recorded hash is on
    the revocation list. A prompt id the run never used is never considered, however its live hash
    has drifted (research R-10: staleness compares only the ids used by THIS run)."""
    if baseline is None:
        return []
    lock = lock_state if lock_state is not None else LockState()
    used: dict[str, str] = {}
    for score in run.metric_scores:
        prompt_id = score.judge_model.prompt_id
        sha = score.judge_model.prompt_sha256
        if prompt_id is not None and sha is not None:
            used[prompt_id] = sha
    stale: list[str] = []
    for prompt_id, current_sha in used.items():
        recorded = baseline.judge_prompt_hashes.get(prompt_id)
        if recorded is None or recorded != current_sha or lock.is_revoked(prompt_id, recorded):
            stale.append(prompt_id)
    return sorted(stale)


def judge_prompt_status_for(
    run: EvaluationRun, baseline: Baseline | None, lock_state: LockState | None = None
) -> JudgePromptStatus:
    """FR-021: `absent` when no baseline exists yet, or one exists but predates this feature (no
    `judge_prompt_hashes` recorded at all) — NEVER silently treated as `ok`. Otherwise `ok` unless
    `stale_judge_prompts_for` names at least one prompt."""
    if baseline is None or not baseline.judge_prompt_hashes:
        return "absent"
    return "BASELINE_STALE" if stale_judge_prompts_for(run, baseline, lock_state) else "ok"


def apply_judge_prompt_status(
    run: EvaluationRun, baseline: Baseline | None, lock_state: LockState | None = None
) -> None:
    """Sets `run.judge_prompt_status` and (only when stale) `run.stale_judge_prompts`, for
    `eval/gate.py::compute_verdict`/`verdict_reason` and the written report to read."""
    status = judge_prompt_status_for(run, baseline, lock_state)
    run.judge_prompt_status = status
    run.stale_judge_prompts = stale_judge_prompts_for(run, baseline, lock_state) if status == "BASELINE_STALE" else []


def baseline_status_for(
    run_judge_model: JudgeModelRecord, baseline: Baseline | None
) -> BaselineStatus:
    """FR-007c: `no_baseline` when none exists yet, `BASELINE_INVALID` on a
    judge-model mismatch (never a false regression from a judge upgrade
    alone), else `ok`."""
    if baseline is None:
        return "no_baseline"
    if baseline.judge_model.model_id != run_judge_model.model_id:
        return "BASELINE_INVALID"
    return "ok"
