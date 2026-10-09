"""T018 — consolidated report assembly, matching
`contracts/eval-report-schema.md` exactly. Applies PII masking
(`observability/redaction.py`) to any conversation-derived text before
assembly (FR-018) — saga-case descriptions and metric details are masked
here, once, at assembly time, not left to each consumer to remember."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eval.gate import compute_verdict
from eval.models import EvaluationRun
from observability.redaction import redact_text

_REPORTS_DIR = Path(__file__).parent / "data" / "reports"


def _mask_text_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Applies `redact_text` to any free-text field a coverage/saga row
    might carry (FR-018) — structured fields (ids, booleans, tier enums)
    pass through untouched."""
    masked = []
    for row in rows:
        masked_row = dict(row)
        for key, value in masked_row.items():
            if isinstance(value, str) and key in ("reason", "description", "detail"):
                masked_row[key] = redact_text(value)
        masked.append(masked_row)
    return masked


def assemble_report(run: EvaluationRun) -> dict[str, Any]:
    """Computes the final verdict (via `eval/gate.py`) and serializes the
    run to the exact shape `contracts/eval-report-schema.md` defines."""
    run.verdict = compute_verdict(run)
    return {
        "run_id": run.run_id,
        "tier": run.tier,
        "code_ref": run.code_ref,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "verdict": run.verdict,
        "judge_model": asdict(run.judge_model),
        "metric_scores": [
            {
                "metric_name": s.metric_name,
                "domain": s.domain,
                "conversation_id": s.conversation_id,
                "step_id": s.step_id,
                "gate_type": s.gate_type,
                "raw_values": s.raw_values,
                "mean": s.mean,
                "variance_adjusted": s.variance_adjusted,
                "judge_model": asdict(s.judge_model),
                "passed": s.passed,
            }
            for s in run.metric_scores
        ],
        "saga_case_results": [
            {"case_id": c.case_id, "passed": c.passed} for c in run.saga_case_results
        ],
        "budget_flags": [
            {"scope": b.scope, "metric": b.metric, "actual": b.actual, "threshold": b.threshold}
            for b in run.budget_flags
        ],
        "active_exemptions": _mask_text_fields(run.active_exemptions),
        "not_evaluated_cases": run.not_evaluated_cases,
        "coverage": run.coverage,
        "baseline_status": run.baseline_status,
        "drift_findings": run.drift_findings,
        "judge_prompt_status": run.judge_prompt_status,
        "stale_judge_prompts": run.stale_judge_prompts,
        "stratum_results": [
            {
                "metric_name": s.metric_name,
                "tenant_id": s.tenant_id,
                "value": s.value,
                "passed": s.passed,
                "blocking": s.blocking,
                "insufficient_data": s.insufficient_data,
            }
            for s in run.stratum_results
        ],
    }


def write_report(run: EvaluationRun, directory: Path = _REPORTS_DIR) -> Path:
    """Persists one report JSON per Evaluation Run (FR-017, ADR-045) —
    `eval/data/reports/<run-id>.json`, a versioned repo file."""
    directory.mkdir(parents=True, exist_ok=True)
    report = assemble_report(run)
    path = directory / f"{run.run_id}.json"
    path.write_text(json.dumps(report, indent=2))
    return path
