"""T027 -- Principle II guard (ADR-053): `langfuse` is imported in exactly one file.

No agent holds a vendor SDK. `prompts/langfuse_adapter.py` is the only module allowed to import it, so the
whole vendor surface can be replaced or removed in one place.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {Path("prompts") / "langfuse_adapter.py"}
SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
IMPORT = re.compile(r"^\s*(?:import\s+langfuse\b|from\s+langfuse(?:\.\w+)*\s+import\b)")


def _python_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*.py")
        if not (set(path.relative_to(ROOT).parts) & SKIP_DIRS)
    ]


def test_langfuse_is_imported_only_by_the_adapter() -> None:
    offenders: list[str] = []
    for path in _python_files():
        rel = path.relative_to(ROOT)
        if rel in ALLOWED:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if IMPORT.match(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "langfuse imported outside prompts/langfuse_adapter.py:\n" + "\n".join(offenders)


def test_the_adapter_itself_does_import_langfuse() -> None:
    """Guards the guard: if the adapter stopped importing it, the rule above would pass vacuously."""
    text = (ROOT / "prompts" / "langfuse_adapter.py").read_text(encoding="utf-8")
    assert any(IMPORT.match(line) for line in text.splitlines())
