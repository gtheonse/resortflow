"""Idempotency-Key store shared by the four holding sims.

Key -> response replay, plus request-fingerprint mismatch detection (spec.md edge
case: the same key reused for a genuinely different request body). See
``specs/002-simulation-ecosystem/data-model.md``'s Idempotency Record entity.

This is the *vendor's* copy of idempotency bookkeeping — distinct from and unrelated to
feature 001's ResortFlow-side ``hold_ledger.idempotency_key``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite
from fastapi import Header

SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    response_body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (idempotency_key, endpoint)
);
"""


async def init_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA)
    await conn.commit()


async def require_idempotency_key(
    idempotency_key: str = Header(..., alias="Idempotency-Key")
) -> str:
    """FastAPI dependency: every mutating endpoint requires an Idempotency-Key header
    per the frozen contracts' shared ``IdempotencyKey`` parameter."""
    return idempotency_key


def fingerprint(payload: object) -> str:
    """Hash of the normalized request body, used to detect a genuinely different
    request reusing the same Idempotency-Key."""
    normalized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class IdempotencyKeyConflict(Exception):
    """The same Idempotency-Key was reused for a request whose fingerprint differs from
    the one it was first used with."""


@dataclass(frozen=True)
class ReplayedResponse:
    status_code: int
    body: dict


async def check_replay(
    conn: aiosqlite.Connection, key: str, endpoint: str, request_fingerprint: str
) -> ReplayedResponse | None:
    """Returns the original response if this (key, endpoint) pair was already used with
    an identical fingerprint (a safe retry). Raises IdempotencyKeyConflict if the key was
    used before with a *different* fingerprint. Returns None if this is a fresh key."""
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        "SELECT * FROM idempotency_records WHERE idempotency_key = ? AND endpoint = ?",
        (key, endpoint),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    if row["request_fingerprint"] != request_fingerprint:
        raise IdempotencyKeyConflict()
    return ReplayedResponse(status_code=row["status_code"], body=json.loads(row["response_body"]))


async def store_response(
    conn: aiosqlite.Connection,
    key: str,
    endpoint: str,
    request_fingerprint: str,
    status_code: int,
    body: dict,
) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO idempotency_records "
        "(idempotency_key, endpoint, request_fingerprint, status_code, response_body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            key,
            endpoint,
            request_fingerprint,
            status_code,
            json.dumps(body),
            datetime.now(UTC).isoformat(),
        ),
    )
    await conn.commit()
