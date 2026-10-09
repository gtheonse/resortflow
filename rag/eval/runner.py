"""T041/T042/T043 — RAGAS-style eval harness.

Three suites, three different assertions, sharing one ``HybridRetriever``:

- ``eval_set.yaml``: context recall / precision against hand-labeled gold
  chunk ids. Exit criterion: context recall >= 0.85 (plan.md).
- ``adversarial_set.yaml``: 200 cross-tenant probes. Exit criterion: zero
  leaked chunks from the probed-for tenant into the caller-tenant result.
- ``negative_set.yaml``: availability-style questions with no gold chunks.
  Exit criterion: the retriever returns zero chunks for every case — via
  ``rag.retriever.is_availability_query``'s deterministic short-circuit,
  not a relevance-score threshold (see that function's docstring).

Context recall (RAGAS's definition, simplified for a fixed gold-chunk-id
set rather than sentence-level attribution): for a case with gold ids G and
retrieved ids R, recall = |G ∩ R| / |G|. Context precision here is the
fraction of retrieved chunks that are in G (a simpler proxy than RAGAS's
rank-weighted version, sufficient for this phase's exit criterion which
only gates on recall).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from rag.models import (
    AdversarialResult,
    EvalCase,
    NegativeResult,
    RetrievalMetrics,
)
from rag.retriever import HybridRetriever

# T079 (2026-09-28): same local-dev-CLI reasoning as `rag/eval/calibrate_floor.py`
# and `ui/run.py` — this module is run directly (`python -m rag.eval.runner`),
# outside docker compose, so it needs its own `load_dotenv()` call or
# `AZURE_OPENAI_*` never reaches `os.environ` and the embedding backend silently
# degrades to the wrong dimension.
load_dotenv()

EVAL_DIR = Path(__file__).parent

# Negative-set cases are availability/live-inventory questions. Retrieval
# relevance alone can't distinguish "cabana available this Saturday?" (no
# static answer) from "what does the Meridian Pool Club include?" (a venue
# description that legitimately mentions cabanas) — a cross-encoder scores
# the first almost as high as the second, because the *content* really is
# topically relevant even though it doesn't *answer* the question. That's
# why HybridRetriever.retrieve_hits short-circuits on
# rag.retriever.is_availability_query() and returns nothing at all for
# these, rather than this module trying to threshold cross-encoder scores
# after the fact — see that function's docstring for the full rationale
# (AGENTS.md's "never put an LLM/relevance-score where deterministic
# pattern matching will do").


def load_cases(path: Path) -> list[EvalCase]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [EvalCase(**case) for case in data["cases"]]


def run_eval_set(
    retriever: HybridRetriever, cases: list[EvalCase], top_k: int = 5
) -> RetrievalMetrics:
    per_case_recall: dict[str, float] = {}
    total_precision_num = 0
    total_precision_den = 0

    for case in cases:
        retrieved = retriever.retrieve(case.tenant_id, case.question, top_k=top_k)
        retrieved_ids = {c.chunk_id for c in retrieved}
        gold_ids = set(case.gold_chunk_ids)

        if gold_ids:
            recall = len(gold_ids & retrieved_ids) / len(gold_ids)
        else:
            recall = 1.0
        per_case_recall[case.id] = recall

        if retrieved_ids:
            total_precision_num += len(gold_ids & retrieved_ids)
            total_precision_den += len(retrieved_ids)

    context_recall = sum(per_case_recall.values()) / len(cases) if cases else 0.0
    context_precision = (
        total_precision_num / total_precision_den if total_precision_den else 0.0
    )

    return RetrievalMetrics(
        context_recall=context_recall,
        context_precision=context_precision,
        num_cases=len(cases),
        per_case_recall=per_case_recall,
    )


def run_adversarial_set(
    retriever: HybridRetriever, cases: list[EvalCase], top_k: int = 10
) -> list[AdversarialResult]:
    results: list[AdversarialResult] = []
    for case in cases:
        caller_tenant = case.query_as_tenant_id or case.tenant_id
        retrieved = retriever.retrieve(caller_tenant, case.question, top_k=top_k)
        leaked = [c.chunk_id for c in retrieved if c.tenant_id != caller_tenant]
        results.append(
            AdversarialResult(case_id=case.id, leaked_chunk_ids=leaked, passed=not leaked)
        )
    return results


def run_negative_set(
    retriever: HybridRetriever, cases: list[EvalCase], top_k: int = 5
) -> list[NegativeResult]:
    results: list[NegativeResult] = []
    for case in cases:
        hits = retriever.retrieve_hits(case.tenant_id, case.question, top_k=top_k)
        returned_ids = [chunk_id for chunk_id, _score, _payload in hits]
        results.append(
            NegativeResult(
                case_id=case.id,
                returned_chunk_ids=returned_ids,
                passed=not returned_ids,
            )
        )
    return results


def run_offtopic_set(retriever: HybridRetriever, cases: list[EvalCase]) -> dict[str, Any]:
    """Feature 010 (FR-021/FR-022, ADR-057): unlike `run_negative_set`
    (availability questions, refused via the deterministic short-circuit),
    off-topic questions are refused by the calibrated relevance floor
    (`graph/routing/thresholds.yaml`, Track C — this function only reads
    it, per analyze finding C2). `knowledge_floor_refusal` is 1.0 iff
    every off-topic case's best score falls below the floor."""
    import yaml

    floor_path = Path(__file__).resolve().parents[2] / "graph" / "routing" / "thresholds.yaml"
    floor = float(yaml.safe_load(floor_path.read_text(encoding="utf-8"))["knowledge_relevance_floor"])

    refused = 0
    for case in cases:
        hits = retriever.retrieve_hits(case.tenant_id, case.question, top_k=3)
        best = max((score for _cid, score, _payload in hits), default=0.0)
        if best < floor:
            refused += 1

    total = len(cases)
    return {
        "knowledge_floor": floor,
        "offtopic_total": total,
        "offtopic_refused": refused,
        "knowledge_floor_refusal": (refused / total) if total else 1.0,
    }


def main() -> None:
    retriever = HybridRetriever()

    eval_cases = load_cases(EVAL_DIR / "eval_set.yaml")
    metrics = run_eval_set(retriever, eval_cases)

    adversarial_cases = load_cases(EVAL_DIR / "adversarial_set.yaml")
    adv_results = run_adversarial_set(retriever, adversarial_cases)
    adv_failures = [r for r in adv_results if not r.passed]

    negative_cases = load_cases(EVAL_DIR / "negative_set.yaml")
    neg_results = run_negative_set(retriever, negative_cases)
    neg_failures = [r for r in neg_results if not r.passed]

    from rag.eval.calibrate_floor import load_offtopic_cases

    offtopic_cases = load_offtopic_cases()
    offtopic_metrics = run_offtopic_set(retriever, offtopic_cases)

    results = {
        "context_recall": metrics.context_recall,
        "context_precision": metrics.context_precision,
        "num_eval_cases": metrics.num_cases,
        "context_recall_target": 0.85,
        "context_recall_pass": metrics.context_recall >= 0.85,
        "adversarial_total": len(adv_results),
        "adversarial_leaks": len(adv_failures),
        "adversarial_pass": not adv_failures,
        "negative_total": len(neg_results),
        "negative_failures": len(neg_failures),
        "negative_pass": not neg_failures,
        "per_case_recall": metrics.per_case_recall,
        **offtopic_metrics,
        "offtopic_pass": offtopic_metrics["knowledge_floor_refusal"] >= 1.0,
    }

    out_path = EVAL_DIR / "results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"context_recall={metrics.context_recall:.3f} (target >=0.85)")
    print(f"context_precision={metrics.context_precision:.3f}")
    print(f"adversarial leaks: {len(adv_failures)}/{len(adv_results)}")
    print(f"negative failures: {len(neg_failures)}/{len(neg_results)}")
    print(
        f"offtopic refused: {offtopic_metrics['offtopic_refused']}/{offtopic_metrics['offtopic_total']} "
        f"(floor={offtopic_metrics['knowledge_floor']})"
    )
    print(f"results written to {out_path}")

    if metrics.context_recall < 0.85 or adv_failures or neg_failures or not results["offtopic_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
