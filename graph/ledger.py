"""Write-ahead hold-ledger client — the Python side of
`infra/sql/001_hold_ledger.sql` (feature 001, ADR-019: "this feature's
single most defensible decision"). The DDL and the ADR's reasoning existed
since Phase 0; no domain wired writes into it until this fix.

The checkpointer is the record of *conversation state*. This ledger is the
record of *external side effects* — Constitution Principle V requires these
never be conflated. A node that is about to place, confirm, or release a
vendor hold writes here *before or alongside* the vendor call, using the
same `idempotency_key` the vendor call itself carries, so a crash at any
point (before the call, after the call but before the state update, or
after) leaves a row a reaper can reconcile — see the SQL file's "THE THREE
CRASH WINDOWS" comment for the exact recovery story.

`HoldLedger` is a `Protocol` so tests can inject a no-op (`NullHoldLedger`)
without standing up Postgres — exactly the pattern `compile_dining_graph`
already uses for its checkpointer parameter. Every domain subgraph (dining
today; entertainment/stay/wellness from feature 005 onward) is expected to
share this module rather than each growing its own ledger client — hence
its home in `graph/`, alongside the other cross-cutting contracts, not in
`agents/dining/`.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Protocol

from graph.models import Domain


class HoldLedger(Protocol):
    """The write-ahead ledger surface every domain subgraph writes through.
    Every method is idempotent-safe: a duplicate `insert_pending` for an
    already-known `idempotency_key` is a no-op (the table's UNIQUE
    constraint is the enforcement; callers never need to pre-check), and a
    `mark_*` on an already-terminal row is a no-op update, never an error —
    resume replays steps, so these calls must tolerate being issued twice.
    """

    async def insert_pending(
        self,
        *,
        saga_id: str,
        session_id: str,
        tenant_id: str,
        domain: Domain,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> None: ...

    async def mark_held(
        self, *, idempotency_key: str, hold_id: str, expires_at: datetime
    ) -> None: ...

    async def mark_confirmed(self, *, idempotency_key: str) -> None: ...

    async def mark_released(self, *, idempotency_key: str) -> None: ...

    async def mark_expired(self, *, idempotency_key: str) -> None: ...

    async def mark_orphan_released(self, *, idempotency_key: str) -> None: ...
    """Feature 005 addition (ADR-022, `contracts/hold-ledger-reaper-contract.md`).
    Transitions a `PENDING`/`HELD` row to `HoldStatus.ORPHAN_RELEASED` — the
    reaper's terminal disposition for a hold whose owning thread crashed
    before the row reached a natural terminal state. Same idempotent-write
    guarantee as every other method here."""


class NullHoldLedger:
    """No-op default. Used when a caller doesn't pass a real ledger (fast
    `MemorySaver`-backed unit tests, or any environment without Postgres
    configured) — mirrors `compile_dining_graph`'s own "no checkpointer ->
    ephemeral in-memory default" graceful degradation. NEVER the default in
    a real deployment path; `compile_dining_graph`'s batteries-included
    default constructs a real `PostgresHoldLedger` whenever `POSTGRES_URL`
    is set, same as it does for the checkpointer."""

    async def insert_pending(self, **_kwargs: object) -> None:
        return None

    async def mark_held(self, **_kwargs: object) -> None:
        return None

    async def mark_confirmed(self, **_kwargs: object) -> None:
        return None

    async def mark_released(self, **_kwargs: object) -> None:
        return None

    async def mark_expired(self, **_kwargs: object) -> None:
        return None

    async def mark_orphan_released(self, **_kwargs: object) -> None:
        return None


class PostgresHoldLedger:
    """Real implementation. Opens a short-lived `psycopg.AsyncConnection`
    per call rather than holding one open for the process lifetime — this
    vertical slice has no connection-pool infrastructure yet (that's a
    reasonable Phase 5+ optimization once more than one domain shares this
    module), and a write-ahead row is, by definition, on the rare path
    relative to read-heavy tool calls."""

    def __init__(self, conn_string: str | None = None) -> None:
        self._conn_string = conn_string or os.environ["POSTGRES_URL"]

    async def _execute(self, query: str, params: dict[str, object]) -> None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
            await conn.commit()

    async def insert_pending(
        self,
        *,
        saga_id: str,
        session_id: str,
        tenant_id: str,
        domain: Domain,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> None:
        # ON CONFLICT DO NOTHING: a resume replaying this node after a
        # crash between INSERT and the vendor call must not raise
        # UniqueViolation on the second attempt — the row already records
        # the same in-flight intent.
        await self._execute(
            """
            INSERT INTO hold_ledger
                (saga_id, session_id, tenant_id, domain, idempotency_key,
                 status, trace_id)
            VALUES
                (%(saga_id)s, %(session_id)s, %(tenant_id)s, %(domain)s,
                 %(idempotency_key)s, 'PENDING', %(trace_id)s)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            {
                "saga_id": saga_id,
                "session_id": session_id,
                "tenant_id": tenant_id,
                "domain": domain.value,
                "idempotency_key": idempotency_key,
                "trace_id": trace_id,
            },
        )

    async def mark_held(
        self, *, idempotency_key: str, hold_id: str, expires_at: datetime
    ) -> None:
        await self._execute(
            """
            UPDATE hold_ledger
            SET status = 'HELD', hold_id = %(hold_id)s, expires_at = %(expires_at)s
            WHERE idempotency_key = %(idempotency_key)s
              AND status = 'PENDING'
            """,
            {
                "idempotency_key": idempotency_key,
                "hold_id": hold_id,
                "expires_at": expires_at,
            },
        )

    async def mark_confirmed(self, *, idempotency_key: str) -> None:
        await self._execute(
            """
            UPDATE hold_ledger SET status = 'CONFIRMED'
            WHERE idempotency_key = %(idempotency_key)s AND status = 'HELD'
            """,
            {"idempotency_key": idempotency_key},
        )

    async def mark_released(self, *, idempotency_key: str) -> None:
        await self._execute(
            """
            UPDATE hold_ledger SET status = 'RELEASED'
            WHERE idempotency_key = %(idempotency_key)s
              AND status IN ('PENDING', 'HELD')
            """,
            {"idempotency_key": idempotency_key},
        )

    async def mark_expired(self, *, idempotency_key: str) -> None:
        await self._execute(
            """
            UPDATE hold_ledger SET status = 'EXPIRED'
            WHERE idempotency_key = %(idempotency_key)s
              AND status IN ('PENDING', 'HELD')
            """,
            {"idempotency_key": idempotency_key},
        )

    async def mark_orphan_released(self, *, idempotency_key: str) -> None:
        await self._execute(
            """
            UPDATE hold_ledger SET status = 'ORPHAN_RELEASED'
            WHERE idempotency_key = %(idempotency_key)s
              AND status IN ('PENDING', 'HELD')
            """,
            {"idempotency_key": idempotency_key},
        )


def default_hold_ledger() -> HoldLedger:
    """`PostgresHoldLedger` when `POSTGRES_URL` is set, else `NullHoldLedger`
    — the same shape of graceful degradation `agents/dining/tracing.py`
    already uses for Langfuse. Callers that need the write-ahead guarantee
    proven (tests, production) construct `PostgresHoldLedger` explicitly
    rather than relying on this."""
    if os.environ.get("POSTGRES_URL"):
        return PostgresHoldLedger()
    return NullHoldLedger()
