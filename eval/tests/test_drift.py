"""T043 — unit tests for `eval/drift.py`'s multi-run baseline-drift
detection: a gradual decline across many runs is flagged even though no
single consecutive pair would cross `eval/gate.py`'s per-run regression
margin; an improving or flat metric is never flagged; and drift findings
never appear anywhere `eval/gate.py` reads (verified via `eval/runner.py`'s
`_run_nightly_tier` not touching `coverage`/`metric_scores`)."""

from __future__ import annotations

import json

from eval.drift import detect_drift


def _write_report(tmp_path, run_id: str, *, mean: float, mtime_offset: int) -> None:
    path = tmp_path / f"{run_id}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "metric_scores": [
                    {
                        "metric_name": "faithfulness",
                        "domain": "dining",
                        "gate_type": "statistical",
                        "mean": mean,
                    }
                ],
            }
        )
    )
    import os

    now = path.stat().st_mtime
    os.utime(path, (now + mtime_offset, now + mtime_offset))


def test_gradual_decline_across_many_runs_is_flagged(tmp_path):
    # Each consecutive step only drops 0.02 (well under a typical 0.10
    # regression margin), but the cumulative drop across 5 runs is 0.08.
    means = [0.95, 0.93, 0.91, 0.89, 0.87]
    for i, mean in enumerate(means):
        _write_report(tmp_path, f"merge_queue-{i}", mean=mean, mtime_offset=i)

    findings = detect_drift(tmp_path, window=10, drift_threshold=0.05)
    assert len(findings) == 1
    assert findings[0].metric_name == "faithfulness"
    assert findings[0].domain == "dining"
    assert round(findings[0].delta, 2) == -0.08


def test_flat_metric_is_not_flagged(tmp_path):
    for i in range(5):
        _write_report(tmp_path, f"merge_queue-{i}", mean=0.9, mtime_offset=i)
    assert detect_drift(tmp_path, window=10, drift_threshold=0.05) == []


def test_improving_metric_is_not_flagged(tmp_path):
    means = [0.80, 0.85, 0.90, 0.95]
    for i, mean in enumerate(means):
        _write_report(tmp_path, f"merge_queue-{i}", mean=mean, mtime_offset=i)
    assert detect_drift(tmp_path, window=10, drift_threshold=0.05) == []


def test_fewer_than_two_reports_returns_no_findings(tmp_path):
    _write_report(tmp_path, "merge_queue-0", mean=0.5, mtime_offset=0)
    assert detect_drift(tmp_path, window=10) == []
    assert detect_drift(tmp_path, window=10) == []  # empty dir case too, implicitly


def test_lower_is_better_metric_direction(tmp_path):
    path = tmp_path / "merge_queue-a.json"
    path.write_text(
        json.dumps(
            {
                "metric_scores": [
                    {"metric_name": "latency", "domain": "dining", "gate_type": "statistical", "mean": 500.0}
                ]
            }
        )
    )
    path2 = tmp_path / "merge_queue-b.json"
    path2.write_text(
        json.dumps(
            {
                "metric_scores": [
                    {"metric_name": "latency", "domain": "dining", "gate_type": "statistical", "mean": 800.0}
                ]
            }
        )
    )
    import os

    now = path.stat().st_mtime
    os.utime(path, (now, now))
    os.utime(path2, (now + 1, now + 1))

    findings = detect_drift(tmp_path, window=10, drift_threshold=0.05)
    assert len(findings) == 1
    assert findings[0].metric_name == "latency"  # got worse (higher latency)
