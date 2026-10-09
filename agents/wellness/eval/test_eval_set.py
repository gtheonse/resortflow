"""pytest entry point for the wellness eval set."""

from __future__ import annotations

import pytest

from agents.wellness.eval.runner import run_all, write_results


@pytest.mark.asyncio
async def test_all_eval_cases_pass():
    summary = await run_all()
    write_results(summary)
    failed = [c for c in summary["cases"] if not c["passed"]]
    assert not failed, f"{len(failed)}/{summary['total']} eval cases failed: {failed}"
    assert summary["total"] == 10
