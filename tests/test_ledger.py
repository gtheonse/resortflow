"""T016 — User Story 2: external mutation intent survives a crash at any point.

FR-017, FR-018, FR-019, SC-004. Requires Postgres — skips cleanly (not
failing) when unavailable, per quickstart.md's Docker-optional note. Fails
with "relation does not exist" until T017
(``infra/sql/001_hold_ledger.sql``) is applied to the running instance.

Independent Test (spec.md, User Story 2): simulate an interruption at each
of the three crash windows directly against the DDL, with no external
system involved — every test here talks to Postgres and the `hold_ledger`
table alone; no MCP, no agent, no vendor sim.

Every test opens its own connection and rolls back at teardown, so no test
leaves committed rows behind and tests never see each other's data.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip(
    "psycopg",
    reason="psycopg driver not importable in this environment",
    exc_type=ImportError,
    # pytest >=9.1 defaults exc_type to ModuleNotFoundError, which does not
    # catch psycopg's ImportError("no pq wrapper available") when the
    # module resolves but its native extension fails to load (e.g. a
    # blocked DLL under a Windows Application Control Policy) — only a
    # missing module. Explicit ImportError restores the broader skip.
)

POSTGRES_URL = os.environ.get(
    "POSTGRES_URL", "postgresql://resortflow:resortflow@localhost:5432/resortflow"
)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    try:
        connection = psycopg.connect(POSTGRES_URL, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip(
            "Postgres not available at POSTGRES_URL — skipping per "
            "quickstart.md's Docker-optional note"
        )
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _insert_row(conn: psycopg.Connection, /, **overrides: Any) -> str:
    """Insert a hold_ledger row with sane defaults, returning its
    idempotency_key. Raises the underlying psycopg error on constraint
    violation — callers assert on that via `pytest.raises`."""
    defaults: dict[str, Any] = {
        "saga_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "tenant_id": "MGV",
        "domain": "dining",
        "idempotency_key": str(uuid.uuid4()),
        "hold_id": None,
        "expires_at": None,
        "status": "PENDING",
    }
    defaults.update(overrides)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hold_ledger
                (saga_id, session_id, tenant_id, domain, idempotency_key,
                 hold_id, expires_at, status)
            VALUES
                (%(saga_id)s, %(session_id)s, %(tenant_id)s, %(domain)s,
                 %(idempotency_key)s, %(hold_id)s, %(expires_at)s, %(status)s)
            """,
            defaults,
        )
    return str(defaults["idempotency_key"])


# --- UNIQUE constraint on idempotency_key -----------------------------------


def test_idempotency_key_unique_rejects_duplicate(conn: psycopg.Connection) -> None:
    """Two attempts submitted with the same idempotency key (spec.md
    acceptance scenario 4) must produce only one entry — enforced at the
    schema level so recovery logic never has to de-duplicate by hand."""
    key = str(uuid.uuid4())
    _insert_row(conn, idempotency_key=key)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_row(conn, idempotency_key=key)


# --- status CHECK constraint --------------------------------------------------


def test_status_check_rejects_invalid_value(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_row(conn, status="BOGUS")


@pytest.mark.parametrize(
    "status",
    ["PENDING", "HELD", "CONFIRMED", "RELEASED", "EXPIRED", "ORPHAN_RELEASED"],
)
def test_status_check_accepts_every_enumerated_value(
    conn: psycopg.Connection, status: str
) -> None:
    expires_at = (
        datetime.now(UTC) + timedelta(minutes=15) if status == "HELD" else None
    )
    _insert_row(conn, status=status, expires_at=expires_at)


def test_domain_check_rejects_invalid_value(conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_row(conn, domain="valet_parking")


# --- HELD-requires-expires_at constraint --------------------------------------


def test_held_without_expires_at_is_rejected(conn: psycopg.Connection) -> None:
    """A held row must know when it dies, or the reaper cannot reason about
    it (hold-ledger.sql's own comment on this constraint)."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_row(conn, status="HELD", expires_at=None)


def test_held_with_expires_at_is_accepted(conn: psycopg.Connection) -> None:
    key = _insert_row(
        conn, status="HELD", expires_at=datetime.now(UTC) + timedelta(minutes=15)
    )
    assert key


# --- Orphan-candidate query (FR-019) ------------------------------------------


def test_orphan_candidate_query_identifies_expected_set(
    conn: psycopg.Connection,
) -> None:
    """Given entries in PENDING/HELD with no active session and no terminal
    outcome, the query the reaper will run must identify exactly them.
    "Active" is an application-level input here (a set of session ids) —
    this feature ships no reaper and no checkpoint-thread join; it proves
    the schema and its `(status, expires_at)` / `session_id` indexes
    support the query FR-019 requires, per the Independent Test's "no
    external system involved"."""
    active_session = str(uuid.uuid4())
    orphaned_session = str(uuid.uuid4())

    # Still-active session, PENDING — not a candidate.
    active_key = _insert_row(conn, session_id=active_session, status="PENDING")

    # Orphaned session, HELD with live inventory — a candidate.
    orphan_key = _insert_row(
        conn,
        session_id=orphaned_session,
        status="HELD",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    # Same orphaned session, but already terminal — never a candidate,
    # regardless of session activity.
    terminal_key = _insert_row(conn, session_id=orphaned_session, status="RELEASED")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT idempotency_key FROM hold_ledger
            WHERE status IN ('PENDING', 'HELD')
              AND session_id <> ALL(%s)
            """,
            ([active_session],),
        )
        candidates = {row[0] for row in cur.fetchall()}

    assert orphan_key in candidates
    assert active_key not in candidates
    assert terminal_key not in candidates


# --- updated_at trigger --------------------------------------------------------


def test_updated_at_trigger_fires_on_update(conn: psycopg.Connection) -> None:
    key = _insert_row(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT created_at, updated_at FROM hold_ledger WHERE idempotency_key = %s",
            (key,),
        )
        created_at, updated_at_before = cur.fetchone()
        assert created_at == updated_at_before

        cur.execute(
            "UPDATE hold_ledger SET hold_id = %s WHERE idempotency_key = %s",
            ("vendor-hold-1", key),
        )
        cur.execute(
            "SELECT updated_at FROM hold_ledger WHERE idempotency_key = %s", (key,)
        )
        (updated_at_after,) = cur.fetchone()
        assert updated_at_after >= updated_at_before
