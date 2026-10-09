"""Feature 010, T064 — sweeps `graph/routing/thresholds.yaml`'s
`knowledge_relevance_floor` over `eval_set.yaml` (answerable) and
`negative_set.yaml`'s `offtopic:` section, and PRINTS the lowest floor
that refuses 100% of off-topic cases while keeping 100% of answerable
ones (SC-008) — it never writes Track C's `graph/routing/thresholds.yaml`
itself (analyze finding C2: a cross-track write belongs to a single
integration commit, not a script run by a different track).

`python -m rag.eval.calibrate_floor` (quickstart Scenario 8)."""

from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv

from rag.eval.runner import EVAL_DIR, load_cases
from rag.models import EvalCase
from rag.retriever import HybridRetriever

# T079 (2026-09-28) — found live: this script is run directly (`python -m
# rag.eval.calibrate_floor`), outside docker compose, the same "local dev CLI"
# shape as `ui/run.py` — which calls `load_dotenv()` for exactly this reason
# (see that module's own docstring finding). Without it, `AZURE_OPENAI_ENDPOINT`/
# `AZURE_OPENAI_API_KEY` are absent from `os.environ`, so `rag.embeddings
# .get_embedding_backend()` silently falls back to the 384-dim local
# sentence-transformers backend against a Qdrant collection dimensioned for the
# Azure embedder's 3072 dims — the exact "Vector dimension error" this feature's
# research.md R-16 had recorded as an unrelated pre-existing environment gap.
# It was not a stack misconfiguration; it was a missing `load_dotenv()` call.
load_dotenv()

_CANDIDATE_FLOORS = [round(0.05 * i, 2) for i in range(1, 20)]  # 0.05 .. 0.95


def load_offtopic_cases(path: Path = EVAL_DIR / "negative_set.yaml") -> list[EvalCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [EvalCase(**case) for case in data["offtopic"]]


def _best_scores(retriever: HybridRetriever, cases: list[EvalCase]) -> list[float]:
    scores: list[float] = []
    for case in cases:
        hits = retriever.retrieve_hits(case.tenant_id, case.question, top_k=3)
        scores.append(max((score for _cid, score, _payload in hits), default=0.0))
    return scores


def calibrate() -> dict:
    retriever = HybridRetriever()
    answerable = load_cases(EVAL_DIR / "eval_set.yaml")
    offtopic = load_offtopic_cases()

    answerable_scores = _best_scores(retriever, answerable)
    offtopic_scores = _best_scores(retriever, offtopic)

    chosen: float | None = None
    for floor in _CANDIDATE_FLOORS:
        answerable_kept = sum(1 for s in answerable_scores if s >= floor) == len(answerable_scores)
        offtopic_refused = sum(1 for s in offtopic_scores if s < floor) == len(offtopic_scores)
        if answerable_kept and offtopic_refused:
            chosen = floor
            break

    return {
        "chosen_floor": chosen,
        "answerable_scores": answerable_scores,
        "offtopic_scores": offtopic_scores,
        "answerable_min": min(answerable_scores) if answerable_scores else None,
        "offtopic_max": max(offtopic_scores) if offtopic_scores else None,
    }


def main() -> None:
    result = calibrate()
    print(f"answerable best-score range: {min(result['answerable_scores']):.3f} - {max(result['answerable_scores']):.3f}")
    print(f"offtopic best-score range:   {min(result['offtopic_scores']):.3f} - {max(result['offtopic_scores']):.3f}")
    if result["chosen_floor"] is not None:
        print(f"chosen floor: {result['chosen_floor']}")
        print(
            "no distribution overlap in the swept range — a floor exists that keeps "
            "100% answerable and refuses 100% offtopic"
        )
    else:
        print("NO FLOOR FOUND that satisfies both 100% answerable and 100% offtopic-refusal —")
        print(f"answerable min score: {result['answerable_min']:.3f}")
        print(f"offtopic max score:   {result['offtopic_max']:.3f}")
        print("recommendation: set the floor to keep 100% answerable and record the gap as a finding.")


if __name__ == "__main__":
    main()
