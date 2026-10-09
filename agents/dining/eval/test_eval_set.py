"""pytest entry point for the T057 eval set — what `make eval` (or plain
`pytest agents/dining`) actually runs. Delegates to `runner.run_all` so
there is exactly one place the 25 cases are interpreted; this file only
asserts on the summary and (re)writes `results.json` so it's always fresh
after a test run.
"""

from __future__ import annotations

import pytest

from agents.dining.eval.runner import run_all, write_results


@pytest.mark.asyncio
async def test_all_25_eval_cases_pass():
    summary = await run_all()
    write_results(summary)
    failed = [c for c in summary["cases"] if not c["passed"]]
    assert not failed, f"{len(failed)}/{summary['total']} eval cases failed: {failed}"
    assert summary["total"] == 25
