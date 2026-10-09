"""T041/T042/T043 — eval harness exit-criteria tests.

These are the phase's actual gate: run the full 60-question eval set, the
200-probe adversarial set, and the negative set against the real indexed
corpus and assert plan.md's exit criteria. Slow (network-free but
CPU-bound: ~290 retrieval calls, each with a cross-encoder rerank pass) —
this is the same computation `python -m rag.eval.runner` does, just
asserted instead of printed.
"""

from __future__ import annotations

from rag.eval.runner import (
    EVAL_DIR,
    load_cases,
    run_adversarial_set,
    run_eval_set,
    run_negative_set,
)
from rag.tests.conftest import requires_qdrant


@requires_qdrant
def test_context_recall_meets_target(retriever) -> None:
    cases = load_cases(EVAL_DIR / "eval_set.yaml")
    assert len(cases) == 60
    metrics = run_eval_set(retriever, cases)
    assert metrics.context_recall >= 0.85, (
        f"context_recall={metrics.context_recall:.3f} below 0.85 target; "
        f"per-case misses: {[k for k, v in metrics.per_case_recall.items() if v < 1.0]}"
    )


@requires_qdrant
def test_zero_cross_tenant_leakage(retriever) -> None:
    cases = load_cases(EVAL_DIR / "adversarial_set.yaml")
    assert len(cases) == 200
    results = run_adversarial_set(retriever, cases)
    failures = [r for r in results if not r.passed]
    assert not failures, f"{len(failures)} adversarial probes leaked cross-tenant chunks: {failures[:5]}"


@requires_qdrant
def test_negative_set_returns_nothing(retriever) -> None:
    cases = load_cases(EVAL_DIR / "negative_set.yaml")
    assert len(cases) == 20
    results = run_negative_set(retriever, cases)
    failures = [r for r in results if not r.passed]
    assert not failures, f"{len(failures)} negative-set cases returned content: {failures}"
