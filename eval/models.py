"""Shared dataclasses for the evaluation gate (data-model.md). Imported by
`eval/gate.py`, `eval/baseline.py`, `eval/report.py`, and the scorers —
kept in one module so the shapes those files pass to each other have a
single definition, per the same "one source of truth" reasoning
`observability/attributes.py` already applies to span attribute names.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


def _now() -> datetime:
    return datetime.now(UTC)

GateTierName = Literal["pr", "merge_queue", "nightly"]
RunVerdict = Literal["PASS", "FAIL", "INCOMPLETE"]
GateType = Literal["binary", "statistical"]
BaselineStatus = Literal["ok", "BASELINE_INVALID", "no_baseline"]
JudgePromptStatus = Literal["ok", "BASELINE_STALE", "absent"]
"""T048/research R-10. `absent` (no baseline, or a baseline captured before this feature added
`judge_prompt_hashes`) is deliberately its own value, distinct from `ok` -- FR-021 requires the report to say
so explicitly rather than silently treating "nothing recorded yet" as "verified clean"."""


@dataclass
class JudgeModelRecord:
    provider: str
    model_id: str
    tier: str = "frontier"
    prompt_id: str | None = None
    """T048/FR-017: which prompt (Git id) produced this judge call, alongside the pinned model. `None` for a
    deterministic (non-judge) metric, which carries no prompt at all."""
    prompt_sha256: str | None = None
    prompt_version: str | None = None


@dataclass
class MetricScore:
    metric_name: str
    domain: str
    conversation_id: str
    gate_type: GateType
    raw_values: list[float]
    judge_model: JudgeModelRecord
    step_id: str | None = None
    tenant_id: str | None = None
    """T048/FR-021a/research R-09: which tenant this case belongs to, so `eval/gate.py` can evaluate floors and
    the baseline-delta per `(metric, tenant)` as well as on the aggregate. `None` is back-compat for scores that
    predate per-tenant gating (e.g. the trajectory metrics) -- they are gated on the aggregate only."""
    passed: bool | None = None
    """Set by `eval/gate.py`; `None` until evaluated."""

    @property
    def mean(self) -> float:
        return statistics.fmean(self.raw_values)

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.raw_values) if len(self.raw_values) > 1 else 0.0

    @property
    def variance_adjusted(self) -> float | None:
        """`mean - stdev` (FR-007a) — only meaningful with >=2 samples
        (the merge-queue tier's 3x repeats); `None` for the PR tier's
        single-run subset, per FR-007a's own carve-out."""
        if len(self.raw_values) < 2:
            return None
        return self.mean - self.stdev


@dataclass
class SagaCaseResult:
    case_id: str
    passed: bool
    domain: str = ""
    failing_step: str = ""


@dataclass
class BudgetFlag:
    scope: str
    metric: Literal["cost", "latency"]
    actual: float
    threshold: float


@dataclass
class StratumResult:
    """T042/research R-09: one gated `(metric, tenant)` cell, or the aggregate cell when
    `tenant_id is None`. Kept separate from `MetricScore` because one stratum aggregates many scores."""

    metric_name: str
    tenant_id: str | None
    value: float
    passed: bool
    blocking: bool = True
    """`False` for a metric in the run's `report_only` set (e.g. `dietary_recall` before T053a's calibration):
    still computed and recorded, but never turns the verdict FAIL on its own (T056)."""
    insufficient_data: bool = False
    """`True` only for a per-tenant stratum below `MIN_CASES_PER_TENANT` — `passed` is forced `False`
    ("never a pass", T042) but this is a COVERAGE gap, not a quality finding, so `eval/gate.py`'s
    `compute_verdict`/`verdict_reason` exclude it from `METRIC_FAILURE` (the coverage row already fails the run)."""


@dataclass
class Baseline:
    source_run_id: str
    judge_model: JudgeModelRecord
    per_metric_mean: dict[str, float] = field(default_factory=dict)
    per_metric_stdev: dict[str, float] = field(default_factory=dict)
    judge_prompt_hashes: dict[str, str] = field(default_factory=dict)
    """T048/FR-016a: `{prompt_id: sha256}` for every judge prompt scored into this baseline. Absent/empty for a
    baseline captured before this feature -- `eval/baseline.py::judge_prompt_status_for` treats that as
    `absent`, never `ok` (FR-021)."""
    per_tenant_mean: dict[str, dict[str, float]] = field(default_factory=dict)
    """T048/research R-09: `{metric_name: {tenant_id: mean}}`."""
    per_tenant_stdev: dict[str, dict[str, float]] = field(default_factory=dict)
    captured_at: datetime = field(default_factory=_now)


@dataclass
class EvaluationRun:
    run_id: str
    tier: GateTierName
    code_ref: str
    judge_model: JudgeModelRecord
    started_at: datetime = field(default_factory=_now)
    completed_at: datetime | None = None
    verdict: RunVerdict = "PASS"
    metric_scores: list[MetricScore] = field(default_factory=list)
    saga_case_results: list[SagaCaseResult] = field(default_factory=list)
    budget_flags: list[BudgetFlag] = field(default_factory=list)
    active_exemptions: list[dict[str, Any]] = field(default_factory=list)
    not_evaluated_cases: list[str] = field(default_factory=list)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    baseline_status: BaselineStatus = "no_baseline"
    drift_findings: list[dict[str, Any]] = field(default_factory=list)
    """Nightly tier only (`eval/drift.py`, T043) — report-only, never read by
    `eval/gate.py`'s `compute_verdict`."""
    judge_prompt_status: JudgePromptStatus = "absent"
    """T041/FR-016a, set by `eval/baseline.py::apply_judge_prompt_status`."""
    stale_judge_prompts: list[str] = field(default_factory=list)
    """Prompt ids the run used whose current hash differs from the baseline's recorded hash (or is revoked)."""
    stratum_results: list[StratumResult] = field(default_factory=list)
    """T042, set by `eval/gate.py::evaluate_strata` — one entry per gated `(metric, tenant)` cell plus the
    aggregate cell (`tenant_id=None`) for every metric that has at least one tenant-tagged score."""
