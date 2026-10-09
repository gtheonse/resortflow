"""T036/T037 — `python -m eval.cli show-conversation --id <id>` (FR-011,
FR-018): the human-diagnosis surface over a `ConversationRecord`.

**Interim lookup strategy** (documented deviation, spec.md Assumptions):
this build has no live database of past conversations addressable by ID
alone — the real target is "reconstruct a LangGraph `config` for
`--thread-id` against the production Postgres checkpointer and call
`build_conversation_record` directly," but that requires a live connection
string (`POSTGRES_URL`) this sandbox cannot exercise in tests. Until that
wiring lands, `show-conversation` reads a previously-saved JSON dump of a
`ConversationRecord` from `eval/data/conversations/<id>.json`. This keeps
FR-012's "single source of truth" property intact: the dump itself is
expected to have been produced by `build_conversation_record` (e.g. by
`eval/runner.py` or an operator script serializing the object after a real
run), never hand-authored — the CLI only ever *renders* what that one
function produced, it does not re-derive conversation state on its own.

FR-018: this CLI must show *masked* content, never a bare denial — every
string field is redacted via `observability.redaction.redact_for_trace`
before printing (T037). If redaction itself fails (e.g. the NER backend is
unavailable), the command still fails closed by refusing to print anything
unmasked, per that module's own fail-closed contract — see
`redact_for_trace_or_suppress`. NOTE: `observability.redaction` transitively
imports `spacy`/`torch` (Presidio's NLP backend) and is known to fail to
import in some sandboxes lacking those native libraries (unrelated to this
module) — that failure surfaces as a clear error here rather than being
silently swallowed.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

from eval.conversation_record import ConversationRecord, Turn

_CONVERSATIONS_DIR = Path(__file__).parent / "data" / "conversations"


def _record_from_dict(payload: dict[str, Any]) -> ConversationRecord:
    turns = [Turn(**turn) for turn in payload.get("turns", [])]
    return ConversationRecord(
        conversation_id=payload["conversation_id"],
        tenant_id=payload["tenant_id"],
        turns=turns,
        source=payload.get("source", "evaluated"),
    )


def load_conversation_record(conversation_id: str, *, directory: Path | None = None) -> ConversationRecord:
    """Load a previously-dumped `ConversationRecord` by id. Raises
    `FileNotFoundError` (with a message pointing at the interim-lookup
    limitation above) when no dump exists for `conversation_id` — this CLI
    never fabricates a record."""
    directory = directory or _CONVERSATIONS_DIR
    path = directory / f"{conversation_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No saved conversation dump at {path}. This build's show-conversation "
            "command only reads pre-dumped ConversationRecord JSON (see eval/cli.py "
            "module docstring) — a live Postgres-checkpointer lookup by thread id "
            "is not wired up in this sandbox."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _record_from_dict(payload)


def _redact_turn(turn: Turn) -> dict[str, Any]:
    """Redact every string field of a `Turn` before it is ever printed
    (FR-018/T037). Uses `redact_for_trace` on the turn's dict form so
    nested tool_calls/retrieved_context payloads are covered too, not just
    the top-level input/output strings."""
    from observability.redaction import redact_for_trace

    payload = dataclasses.asdict(turn)
    return redact_for_trace(payload)


def _print_turn(index: int, turn: Turn) -> None:
    redacted = _redact_turn(turn)
    print(f"--- turn {index} ---")
    print(f"input: {redacted['input']}")
    print(f"decisions: {redacted['decisions']}")
    print(f"tool_calls: {redacted['tool_calls']}")
    print(f"retrieved_context: {redacted['retrieved_context']}")
    print(f"output: {redacted['output']}")
    print(f"cost: {redacted['cost']}")
    print(f"latency_ms: {redacted['latency_ms']}")


def show_conversation(conversation_id: str, *, directory: Path | None = None) -> int:
    try:
        record = load_conversation_record(conversation_id, directory=directory)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"conversation_id: {record.conversation_id}")
    print(f"tenant_id: {record.tenant_id}")
    print(f"source: {record.source}")
    for index, turn in enumerate(record.turns):
        _print_turn(index, turn)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser(
        "show-conversation", help="Print a redacted ConversationRecord by id"
    )
    show_parser.add_argument("--id", dest="conversation_id", required=True)

    args = parser.parse_args(argv)

    if args.command == "show-conversation":
        return show_conversation(args.conversation_id)
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover — argparse.error already raises SystemExit


if __name__ == "__main__":
    raise SystemExit(main())
