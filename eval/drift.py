"""T043 — nightly-tier multi-run baseline-drift detection (FR-019 nightly
bullet; ADR-042's Consequences explicitly assigns this responsibility here,
distinct from `eval/gate.py`'s per-run delta check, which only ever compares
one run against the single current baseline and cannot see a slow decline
spread across many runs that never individually crosses the regression
margin).

Report-only: nothing in this module ever raises a gate failure or writes
`not_evaluated_cases` — the nightly tier never blocks a merge (FR-019).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPORTS_DIR = Path(__file__).parent / "data" / "reports"


@dataclass
class DriftFinding:
    metric_name: str
    domain: str
    oldest_mean: float
    newest_mean: float
    delta: float
    run_count: int


def _load_recent_merge_queue_reports(
    reports_dir: Path, *, window: int
) -> list[dict[str, Any]]:
    """Merge-queue reports only (PR-tier runs are too sparse/stratified a
    sample to trend meaningfully), oldest-first, most recent `window`."""
    paths = sorted(
        reports_dir.glob("merge_queue-*.json"), key=lambda p: p.stat().st_mtime
    )
    reports = [json.loads(p.read_text()) for p in paths[-window:]]
    return reports


def detect_drift(
    reports_dir: Path = _REPORTS_DIR, *, window: int = 10, drift_threshold: float = 0.05
) -> list[DriftFinding]:
    """Compares each statistical metric's mean in the OLDEST vs. NEWEST
    report in the window (a simple, explainable trend signal — not a
    regression fit, which would be overkill for a nightly report nobody is
    gating on). Flags a `(metric, domain)` pair whose mean has moved by more
    than `drift_threshold` in the "worse" direction across the window, even
    though no single consecutive pair of runs ever crossed
    `eval/gate.py`'s per-run regression margin.
    """
    reports = _load_recent_merge_queue_reports(reports_dir, window=window)
    if len(reports) < 2:
        return []

    oldest, newest = reports[0], reports[-1]
    oldest_by_key = {
        (s["metric_name"], s["domain"]): s["mean"]
        for s in oldest["metric_scores"]
        if s["gate_type"] == "statistical"
    }
    newest_by_key = {
        (s["metric_name"], s["domain"]): s["mean"]
        for s in newest["metric_scores"]
        if s["gate_type"] == "statistical"
    }

    findings: list[DriftFinding] = []
    for key, newest_mean in newest_by_key.items():
        oldest_mean = oldest_by_key.get(key)
        if oldest_mean is None:
            continue
        metric_name, domain = key
        lower_is_better = metric_name in {"cost", "latency", "unnecessary_step_rate"}
        delta = newest_mean - oldest_mean
        worsened = delta > drift_threshold if lower_is_better else delta < -drift_threshold
        if worsened:
            findings.append(
                DriftFinding(
                    metric_name=metric_name,
                    domain=domain,
                    oldest_mean=oldest_mean,
                    newest_mean=newest_mean,
                    delta=delta,
                    run_count=len(reports),
                )
            )
    return findings
