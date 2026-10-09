-- ResortFlow write-ahead hold ledger
-- Feature: 001-contracts-foundation
-- Permanent home at implementation: infra/sql/001_hold_ledger.sql
-- Source: docs/ResortFlow-HITL-Durability-State.md §3
--
-- WHY THIS TABLE EXISTS
-- ---------------------
-- The LangGraph checkpointer is a system of record for CONVERSATION STATE.
-- It is not a system of record for EXTERNAL SIDE EFFECTS. Two reasons:
--
--   1. Under durability="sync", the persistence order of put_writes (a completed
--      task's pending writes) and put (the superseding checkpoint) is not enforced
--      -- both are submitted to the same background executor thread pool. Whether a
--      crash during put() leaves the task's writes durable is decided by the OS
--      thread scheduler.
--   2. On resume the workflow does not continue from the same line of code. It
--      identifies a starting point and REPLAYS all steps from there, re-running
--      tasks and relying on recorded outcomes.
--
-- Together those mean a node that placed a vendor hold can re-execute. Without an
-- independent record, a partial fan-out failure double-books on every retry.
--
-- THE THREE CRASH WINDOWS THIS TABLE CLOSES
-- -----------------------------------------
--   Crash after INSERT intent, before the vendor call
--       -> status='PENDING', hold_id IS NULL
--       -> reaper probes the vendor by idempotency_key; releases if it exists
--   Crash after the vendor call, before UPDATE outcome
--       -> status='PENDING', hold_id IS NULL  (indistinguishable from the above,
--          which is exactly why the idempotency key -- not the vendor's hold_id --
--          is the join key)
--       -> same recovery path
--   Crash after UPDATE outcome
--       -> status='HELD' with hold_id
--       -> reaper releases directly
--
-- A reaper runs every 60s: any row in PENDING or HELD whose saga_id has no active
-- thread and no terminal outcome is released and marked ORPHAN_RELEASED.

CREATE TABLE IF NOT EXISTS hold_ledger (
    ledger_id        BIGSERIAL PRIMARY KEY,

    -- Correlation. saga_id deliberately outlives trace_id: a guest holds in turn 3,
    -- approves in turn 5, compensation runs in turn 6 -- three traces, one saga.
    saga_id          TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    tenant_id        TEXT NOT NULL,
    domain           TEXT NOT NULL,

    -- THE join key across every crash window. Client-generated from
    -- (session_id, hold_id, attempt) BEFORE the vendor is called, which is what
    -- makes an in-flight attempt identifiable at all.
    idempotency_key  TEXT NOT NULL UNIQUE,

    -- Vendor-assigned. NULL until the vendor responds -- this nullability is the
    -- whole point and must not be "fixed" with a placeholder.
    hold_id          TEXT,
    expires_at       TIMESTAMPTZ,

    status           TEXT NOT NULL,

    -- Correlates a ledger row to its telemetry. See observability/attributes.py.
    trace_id         TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT hold_ledger_status_valid CHECK (
        status IN ('PENDING', 'HELD', 'CONFIRMED', 'RELEASED', 'EXPIRED', 'ORPHAN_RELEASED')
    ),

    -- A held row must know when it dies, or the reaper cannot reason about it.
    CONSTRAINT hold_ledger_held_has_expiry CHECK (
        status <> 'HELD' OR expires_at IS NOT NULL
    ),

    CONSTRAINT hold_ledger_domain_valid CHECK (
        domain IN ('dining', 'entertainment', 'stay', 'wellness', 'gaming')
    )
);

-- The reaper's access path: find live-inventory rows that have aged out.
CREATE INDEX IF NOT EXISTS hold_ledger_status_expires_idx
    ON hold_ledger (status, expires_at);

-- Saga reconstruction and compensation.
CREATE INDEX IF NOT EXISTS hold_ledger_saga_idx
    ON hold_ledger (saga_id);

-- Resume-time revalidation: "what did this session have outstanding?"
CREATE INDEX IF NOT EXISTS hold_ledger_session_idx
    ON hold_ledger (session_id);

CREATE OR REPLACE FUNCTION hold_ledger_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS hold_ledger_set_updated_at ON hold_ledger;
CREATE TRIGGER hold_ledger_set_updated_at
    BEFORE UPDATE ON hold_ledger
    FOR EACH ROW
    EXECUTE FUNCTION hold_ledger_touch_updated_at();

COMMENT ON TABLE hold_ledger IS
    'Write-ahead record of external mutation intent and outcome. Owned by ResortFlow, '
    'deliberately separate from the LangGraph checkpoint tables. The checkpointer '
    'owns the conversation; this table owns the side effects.';

COMMENT ON COLUMN hold_ledger.idempotency_key IS
    'Client-generated before the vendor call. The join key across all three crash '
    'windows -- this is why it, and not hold_id, is the merge key in state.';

COMMENT ON COLUMN hold_ledger.hold_id IS
    'Vendor-assigned; NULL until the vendor responds. The nullability is load-bearing.';
