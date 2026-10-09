"""T016 -- canonical serialisation and content hash (research R-05, ADR-053).

The SHA-256 of a prompt's *content* is the identity the runtime allow-list trusts. It covers the
messages (role + content only) and the config, and never name, version, labels, tags or
`commit_message`, so identical content hashes identically whichever source it came from (FR-005).

    sha256( utf8( json.dumps({"type": "chat", "messages": [{"role", "content"}...], "config": {...}},
                              sort_keys=True, separators=(",", ":"), ensure_ascii=False) ) )

Line endings in message content are normalised to LF first (universal newlines: CRLF and a lone CR both become
LF), so a Windows checkout and a Linux checkout agree. The normalisation is IDEMPOTENT -- no CR survives it --
which is what makes "identical content hashes identically regardless of line endings" (FR-005) hold for ANY
text. A single-pass CRLF -> LF replacement is not idempotent (CR CR LF needs two passes), and Hypothesis found
exactly that counterexample. Extra keys on a message (a registry chat message carries `type`) are ignored.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prompts.schema import PromptTemplate

_NEWLINES = re.compile(r"\r\n?")


def canonical_json(messages: Sequence[Mapping[str, Any]], config: Mapping[str, Any]) -> str:
    payload: dict[str, Any] = {
        "type": "chat",
        "messages": [
            {"role": m["role"], "content": _NEWLINES.sub("\n", str(m["content"]))} for m in messages
        ],
        "config": dict(config),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(messages: Sequence[Mapping[str, Any]], config: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(messages, config).encode("utf-8")).hexdigest()


def template_sha256(template: PromptTemplate) -> str:
    return sha256_of([{"role": m.role, "content": m.content} for m in template.messages], template.config)
