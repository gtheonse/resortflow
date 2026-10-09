"""Feature 008: verifies `observability.telemetry._instrument_openai` actually
wires `opentelemetry-instrumentation-openai-v2` so every chat-completions call
emits `gen_ai.usage.input_tokens`/`gen_ai.usage.output_tokens`/
`gen_ai.request.model`, nested under whichever `start_agent_span` is current
— rather than trusting the library's own README.

Runs the real check in a fresh subprocess (`tests/_telemetry_openai_probe.py`)
rather than in-process: `OpenAIInstrumentor` binds its patched client method to
whichever `TracerProvider` is live the first time `_instrument_openai`
succeeds, for the life of the process, so running this in the shared pytest
session would pass or fail depending on which other test happened to trigger
`observability.telemetry._tracer()` first — a real, verified failure mode
(confirmed while writing this test: it fails when collected after
`eval/tests/test_telemetry.py` in the same session, and passes standalone).
A subprocess sidesteps that instead of hiding it.

Uses a mocked httpx transport — no live Azure/Foundry credentials or network
access required, matching this repo's stated cloud-agnostic testing posture
(feature 007, FR-013).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_PROBE = Path(__file__).parent / "_telemetry_openai_probe.py"
_REPO_ROOT = Path(__file__).parent.parent


def test_openai_call_emits_usage_and_model_nested_under_node_span() -> None:
    result = subprocess.run(
        [sys.executable, str(_PROBE)],
        check=False,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"probe failed (exit {result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout
