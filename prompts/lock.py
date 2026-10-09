"""T030 -- the runtime half of the prompt lock (contracts/lock-and-revocations.md; FR-013/FR-014/FR-014a).

Two append-only JSON files are shipped in the image:

* `prompts/prompts.lock.json`  -- the approved content hashes per prompt id;
* `prompts/revocations.json`   -- tombstones: hashes that must never be served again.

A registry version is servable iff its hash is approved AND not revoked. Everything here is pure file
loading -- no registry access -- and it fails CLOSED:

* a missing lock file means an empty approved set (the registry can serve nothing; Git still works);
* a missing revocations file means an empty revoked list;
* a MALFORMED file raises `LockError` (startup validation surfaces it), and when the runtime has to keep
  going the state is marked `broken`, which refuses all registry content rather than risk serving a hash a
  corrupt revocation file could not veto.

A revoked hash is refused for EVERY prompt id: identical content is identical content.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent
LOCK_PATH = PROMPTS_DIR / "prompts.lock.json"
REVOCATIONS_PATH = PROMPTS_DIR / "revocations.json"

_REQUIRED_TOMBSTONE_FIELDS = ("sha256", "prompt_id", "reason", "date", "ref")


class LockError(ValueError):
    """The lock or revocation file exists but is malformed."""


@dataclass(frozen=True)
class LockState:
    approved: Mapping[str, frozenset[str]] = field(default_factory=dict)
    revoked: Mapping[str, frozenset[str]] = field(default_factory=dict)
    broken: bool = False

    def is_approved(self, prompt_id: str, sha256: str) -> bool:
        if self.broken:
            return False
        return sha256 in self.approved.get(prompt_id, frozenset())

    def is_revoked(self, prompt_id: str, sha256: str) -> bool:  # `prompt_id` unused: revocation is by hash
        return any(sha256 in hashes for hashes in self.revoked.values())


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LockError(f"{path.name}: unreadable or invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LockError(f"{path.name}: expected a JSON object")
    return data


def load_lock(path: Path = LOCK_PATH) -> dict[str, frozenset[str]]:
    """`{prompt_id: approved hashes}`; a missing file is an empty mapping."""
    data = _read_json(path)
    if data is None:
        return {}
    prompts = data.get("prompts")
    if not isinstance(prompts, dict):
        raise LockError(f"{path.name}: expected a 'prompts' object")
    approved: dict[str, frozenset[str]] = {}
    for prompt_id, entry in prompts.items():
        items = entry.get("approved") if isinstance(entry, dict) else None
        if not isinstance(items, list):
            raise LockError(f"{path.name}: prompt {prompt_id!r} needs an 'approved' list")
        hashes: set[str] = set()
        for item in items:
            sha = item.get("sha256") if isinstance(item, dict) else None
            if not isinstance(sha, str) or len(sha) != 64:
                raise LockError(f"{path.name}: prompt {prompt_id!r} has an approved entry without a 64-hex sha256")
            hashes.add(sha)
        approved[prompt_id] = frozenset(hashes)
    return approved


def load_revocations(path: Path = REVOCATIONS_PATH) -> dict[str, frozenset[str]]:
    """`{prompt_id: revoked hashes}`; a missing file is an empty mapping."""
    data = _read_json(path)
    if data is None:
        return {}
    entries = data.get("revoked")
    if not isinstance(entries, list):
        raise LockError(f"{path.name}: expected a 'revoked' list")
    revoked: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise LockError(f"{path.name}: tombstones must be objects")
        missing = [k for k in _REQUIRED_TOMBSTONE_FIELDS if not entry.get(k)]
        if missing:
            raise LockError(f"{path.name}: tombstone is missing {missing}")
        revoked.setdefault(str(entry["prompt_id"]), set()).add(str(entry["sha256"]))
    return {prompt_id: frozenset(hashes) for prompt_id, hashes in revoked.items()}


def load_state(lock_path: Path = LOCK_PATH, revocations_path: Path = REVOCATIONS_PATH) -> LockState:
    """Both files together. Raises `LockError` if either is malformed."""
    return LockState(approved=load_lock(lock_path), revoked=load_revocations(revocations_path))


def is_servable(prompt_id: str, sha256: str, *, state: LockState | None = None) -> bool:
    """approved AND not revoked."""
    current = state if state is not None else load_state()
    return current.is_approved(prompt_id, sha256) and not current.is_revoked(prompt_id, sha256)
