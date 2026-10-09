"""`python -m prompts.sync <command>` -- the prompt tooling CLI (contracts/prompts-cli.md).

Thin wrapper over library code in `prompts/` (mirroring ADR-046: logic lives in the package, workflows only
decide *when*). This module currently provides `validate`; the registry subcommands (push, lock, verify,
promote, revoke, codeowners) arrive with user story 3.

Exit codes: 0 clean, 1 findings (blocking), 2 usage or configuration error.

`validate` is offline. It runs in PR CI and, via `prompts.registry.validate_all()`, at process start of the
orchestrator: an invalid or revoked bundled template must fail fast rather than at the first guest turn.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


def cmd_validate() -> int:
    """Load every template and run the template checks (V1-V9) plus the lint."""
    from prompts.lint import LintConfigError, lint_all, load_exemptions
    from prompts.lock import LockError
    from prompts.registry import PromptError, validate_all
    from prompts.schema import TemplateError, load_all_templates

    try:
        templates = load_all_templates()
    except TemplateError as exc:
        print(f"template error: {exc}", file=sys.stderr)
        return EXIT_FINDINGS
    except OSError as exc:
        print(f"cannot read templates: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        exemptions = load_exemptions()
    except LintConfigError as exc:
        print(f"lint configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    problems: list[str] = []
    try:
        # Imports the call-site modules first so their `declare_required` declarations are visible.
        validate_all(import_call_sites=True)
    except (PromptError, LockError, TemplateError) as exc:
        problems.append(str(exc))
    problems.extend(str(finding) for finding in lint_all(templates, exemptions))

    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return EXIT_FINDINGS
    print(f"ok: {len(templates)} template(s) valid")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m prompts.sync", description="Prompt registry tooling.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="load and validate every template, run the lint (offline)")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate()
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse rejects it first
    return EXIT_USAGE  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
