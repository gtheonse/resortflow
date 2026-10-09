"""ticketing-sim — Ticketmaster-equivalent Event & Ticketing API.

Implements `docs/contracts/openapi/ticketing-sim.openapi.yaml` exactly. Auth: OAuth2
client_credentials bearer PLUS an HMAC-SHA256 request signature (`X-TM-Signature`) — the
heaviest auth burden of the five sims, deliberately distinct from every other scheme
(FR-007/SC-009). Error envelope: `{"fault": {"type", "message", "detail", "retry_after_seconds"}}`.

Implements US1's synchronous surface (events, inventory, seat holds) and US5's
asynchronous purchase path (`POST /orders` -> `202` -> webhook, T034).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import aiosqlite
import httpx
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sims._shared import holds, idempotency
from sims._shared.app import create_app, log_event, make_db_dependency, safe_unlink
from sims._shared.faults import active_fault, add_fault_middleware
from sims._shared.idempotency import require_idempotency_key
from sims._shared.tenancy import require_tenant_id
from sims._shared.webhook_harness import build_webhook_sink_router

DB_PATH = Path(__file__).parent / "data" / "state.db"
TTL_DEFAULT_SECONDS = 480
TTL_MAX_SECONDS = 600

CLIENT_ID = "ticketing-sim-client"
CLIENT_SECRET = "ticketing-sim-client-secret"
HMAC_SECRET = "ticketing-sim-hmac-shared-secret"
TOKEN_TTL_SECONDS = 3600

_issued_tokens: dict[str, float] = {}
_bearer = HTTPBearer()


def check_ticketing_bearer(credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]) -> str:
    token = credentials.credentials
    expiry = _issued_tokens.get(token)
    if expiry is None or expiry < time.time():
        raise HTTPException(status_code=401, detail={"fault": {"type": "SIGNATURE_INVALID", "message": "invalid or expired access token"}})
    return token


def canonical_signature_payload(raw_body: bytes) -> bytes:
    """Canonicalize the request body before signing: parse-and-re-serialize with sorted
    keys and no incidental whitespace, so the signature is stable across equivalent JSON
    encodings (key order, spacing) rather than depending on the literal wire bytes a
    given HTTP client happened to produce. A real HMAC-over-raw-bytes scheme would not
    do this, but it is what makes this signature scheme independently reproducible by
    any caller (including this feature's own contract test suite) without coupling to
    one specific JSON serializer's exact output."""
    import json

    try:
        parsed = json.loads(raw_body) if raw_body else {}
    except ValueError:
        return raw_body
    return json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_signature(raw_body: bytes) -> str:
    payload = canonical_signature_payload(raw_body)
    return hmac.new(HMAC_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()


async def check_signature(request: Request, x_tm_signature: Annotated[str | None, Header(alias="X-TM-Signature")] = None) -> str:
    if x_tm_signature is None:
        raise HTTPException(status_code=401, detail={"fault": {"type": "SIGNATURE_INVALID", "message": "missing X-TM-Signature"}})
    raw_body = await request.body()
    expected = compute_signature(raw_body)
    if not secrets.compare_digest(x_tm_signature, expected):
        raise HTTPException(status_code=401, detail={"fault": {"type": "SIGNATURE_INVALID", "message": "signature mismatch"}})
    return x_tm_signature


def fault(type_: str, message: str, detail: str = "", retry_after_seconds: int | None = None) -> dict:
    return {"fault": {"type": type_, "message": message, "detail": detail, "retry_after_seconds": retry_after_seconds}}


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    classification TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    doors_utc TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    venue_id TEXT NOT NULL,
    venue_name TEXT NOT NULL,
    on_property INTEGER NOT NULL,
    location_zone TEXT NOT NULL,
    address TEXT NOT NULL,
    price_min REAL NOT NULL,
    price_max REAL NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    age_restriction TEXT NOT NULL,
    dress_code TEXT NOT NULL,
    refundable INTEGER NOT NULL,
    PRIMARY KEY (event_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS sections (
    section_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    price_per_seat REAL NOT NULL,
    fees_per_seat REAL NOT NULL,
    capacity INTEGER NOT NULL,
    view_quality TEXT NOT NULL,
    PRIMARY KEY (section_id, event_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hold_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    total_charged REAL NOT NULL,
    seats TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_reason TEXT,
    callback_url TEXT
);
CREATE TABLE IF NOT EXISTS webhook_registrations (
    tenant_id TEXT NOT NULL,
    callback_url TEXT NOT NULL,
    events TEXT NOT NULL,
    secret TEXT
);
"""


async def init_db(db_path: Path) -> None:
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.executescript(SCHEMA)
        await holds.init_schema(conn)
        await idempotency.init_schema(conn)
        await conn.commit()
    finally:
        await conn.close()


async def ensure_seeded(db_path: Path) -> None:
    from sims.ticketing import seed as seed_mod

    fresh = not db_path.exists()
    await init_db(db_path)
    if fresh:
        conn = await aiosqlite.connect(db_path)
        try:
            await seed_mod.seed(conn)
        finally:
            await conn.close()


async def reset(db_path: Path) -> None:
    await safe_unlink(db_path)
    _issued_tokens.clear()
    await ensure_seeded(db_path)


get_db = make_db_dependency(DB_PATH)

app = create_app(
    sim_name="ticketing",
    db_path=DB_PATH,
    ensure_seeded=ensure_seeded,
    reset=reset,
    title="ticketing-sim",
    start_reaper=True,
)

add_fault_middleware(
    app,
    sim_name="ticketing",
    supported_faults={"timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "webhook_delay", "schema_drift"},
    envelope_fn=lambda code, message: fault(code, message),
)

app.include_router(build_webhook_sink_router())

PREFIX = "/discovery/v2"

# Bounded retry-with-backoff for fulfillment webhook delivery (FR-006/spec.md
# Clarifications: "retries happen and are eventually exhausted rather than retried
# forever"). Exact counts/backoff are an implementation-plan detail research.md leaves
# open. When X-Sim-Fault: webhook_delay is set on the triggering purchase, the first
# delivery attempt is forced to fail so the retry path is demonstrably exercised.
WEBHOOK_MAX_ATTEMPTS = 3
WEBHOOK_RETRY_BACKOFFS_SECONDS = [0.1, 0.2, 0.4]


@app.post("/oauth/token")
async def issue_token(request: Request) -> dict:
    form = await request.form()
    grant_type = form.get("grant_type")
    client_id = form.get("client_id")
    client_secret = form.get("client_secret")
    if grant_type != "client_credentials" or not (
        secrets.compare_digest(str(client_id), CLIENT_ID) and secrets.compare_digest(str(client_secret), CLIENT_SECRET)
    ):
        raise HTTPException(status_code=401, detail={"fault": {"type": "SIGNATURE_INVALID", "message": "invalid client credentials"}})
    token = uuid.uuid4().hex
    _issued_tokens[token] = time.time() + TOKEN_TTL_SECONDS
    return {"access_token": token, "token_type": "bearer", "expires_in": TOKEN_TTL_SECONDS}


def _event_to_contract(r: aiosqlite.Row) -> dict:
    return {
        "event_id": r["event_id"],
        "name": r["name"],
        "classification": r["classification"],
        "start_utc": r["start_utc"],
        "doors_utc": r["doors_utc"],
        "duration_minutes": r["duration_minutes"],
        "venue": {
            "venue_id": r["venue_id"],
            "name": r["venue_name"],
            "on_property": bool(r["on_property"]),
            "location_zone": r["location_zone"],
            "address": r["address"],
        },
        "price_range": {"min": r["price_min"], "max": r["price_max"], "currency": r["currency"]},
        "status": r["status"],
    }


@app.get(PREFIX + "/events")
async def search_events(
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    start_utc: str,
    end_utc: str,
    scope: str = "all",
    classification: str | None = None,
    max_price: float | None = None,
    page_token: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM events WHERE tenant_id = ?", (tenant_id,))
    rows = await cur.fetchall()
    await cur.close()
    events = []
    for r in rows:
        if not (start_utc <= r["start_utc"] <= end_utc):
            continue
        if scope == "on_property" and not r["on_property"]:
            continue
        if scope == "city_wide" and r["on_property"]:
            continue
        if classification is not None and r["classification"] != classification:
            continue
        if max_price is not None and r["price_min"] > max_price:
            continue
        events.append(_event_to_contract(r))
    return {"events": events, "next_page_token": None}


@app.get(PREFIX + "/events/{event_id}")
async def get_event(
    event_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM events WHERE event_id = ? AND tenant_id = ?", (event_id, tenant_id))
    r = await cur.fetchone()
    await cur.close()
    if r is None:
        raise HTTPException(status_code=404, detail="event not found")
    detail = _event_to_contract(r)
    detail.update(
        {
            "description": r["description"],
            "age_restriction": r["age_restriction"],
            "dress_code": r["dress_code"],
            "refundable": bool(r["refundable"]),
        }
    )
    return detail


@app.get(PREFIX + "/events/{event_id}/inventory")
async def get_inventory(
    event_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    quantity: int,
    contiguous_only: bool = True,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM sections WHERE event_id = ? AND tenant_id = ?", (event_id, tenant_id))
    rows = await cur.fetchall()
    await cur.close()
    now = datetime.now(UTC)
    sections = []
    for r in rows:
        blocked = await holds.blocking_quantity(db, tenant_id, {"event_id": event_id, "section_id": r["section_id"]}, now)
        available = r["capacity"] - blocked
        if available < quantity and contiguous_only:
            continue
        sections.append(
            {
                "section_id": r["section_id"],
                "name": r["name"],
                "price_per_seat": r["price_per_seat"],
                "fees_per_seat": r["fees_per_seat"],
                "available": available,
                "contiguous_available": available,
                "view_quality": r["view_quality"],
            }
        )
    return {"event_id": event_id, "sections": sections}


def _seat_hold_to_contract(hold: holds.Hold, total_amount: float) -> dict:
    status_map = {"active": "active", "confirmed": "converted", "released": "released", "expired": "expired"}
    return {
        "hold_id": hold.hold_id,
        "event_id": hold.resource_ref["event_id"],
        "section_id": hold.resource_ref["section_id"],
        "quantity": hold.resource_ref.get("quantity", 1),
        "total_amount": total_amount,
        "status": status_map[hold.status],
        "expires_at": hold.expires_at.isoformat(),
    }


@app.post(PREFIX + "/reservations")
async def hold_seats(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    _sig: Annotated[str, Depends(check_signature)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse:
    body = await request.json()
    for field in ("event_id", "section_id", "quantity"):
        if field not in body:
            return JSONResponse(status_code=422, content=fault("INVENTORY_UNAVAILABLE", f"missing {field}"))
    requested_ttl = body.get("ttl_seconds")
    ttl_seconds = min(requested_ttl if requested_ttl is not None else TTL_DEFAULT_SECONDS, TTL_MAX_SECONDS)
    now = datetime.now(UTC)

    cur = await db.execute(
        "SELECT * FROM sections WHERE event_id = ? AND section_id = ? AND tenant_id = ?",
        (body["event_id"], body["section_id"], tenant_id),
    )
    section = await cur.fetchone()
    await cur.close()
    price_per_seat = section["price_per_seat"] + section["fees_per_seat"] if section else 0.0
    capacity = section["capacity"] if section else 0

    blocked = await holds.blocking_quantity(db, tenant_id, {"event_id": body["event_id"], "section_id": body["section_id"]}, now)
    if blocked + body["quantity"] > capacity or active_fault(request) == "inventory_race":
        return JSONResponse(status_code=409, content=fault("INVENTORY_UNAVAILABLE", "seats taken during the turn (inventory race)"))

    resource_ref = {"event_id": body["event_id"], "section_id": body["section_id"], "quantity": body["quantity"]}
    hold = await holds.place_hold(
        db, tenant_id=tenant_id, resource_ref=resource_ref, ttl_seconds=ttl_seconds,
        idempotency_key=idempotency_key, now=now,
    )
    log_event("hold_placed", sim="ticketing", hold_id=hold.hold_id, tenant_id=tenant_id)
    total_amount = price_per_seat * body["quantity"]
    return JSONResponse(status_code=201, content=_seat_hold_to_contract(hold, total_amount))


@app.get(PREFIX + "/reservations/{hold_id}")
async def get_seat_hold(
    hold_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse:
    hold = await holds.get_hold(db, hold_id, tenant_id)
    now = datetime.now(UTC)
    if hold is None or (hold.status == "active" and hold.expires_at <= now):
        return JSONResponse(status_code=404, content=fault("HOLD_EXPIRED", "expired"))
    return JSONResponse(status_code=200, content=_seat_hold_to_contract(hold, 0.0))


@app.delete(PREFIX + "/reservations/{hold_id}", status_code=204)
async def release_seat_hold(
    request: Request,
    hold_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    db: aiosqlite.Connection = Depends(get_db),
) -> None:
    now = datetime.now(UTC)
    try:
        await holds.release_hold(
            db, hold_id, tenant_id, now=now, force_expired=active_fault(request) == "hold_expired"
        )
    except (holds.HoldNotFound, holds.HoldExpiredConflict, holds.HoldTerminalConflict):
        return
    log_event("hold_released", sim="ticketing", hold_id=hold_id, tenant_id=tenant_id)
    return


# ---------------------------------------------------------------------------
# US5: asynchronous purchase path (T034)
# ---------------------------------------------------------------------------


async def _latest_callback_url() -> str | None:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    try:
        cur = await conn.execute(
            "SELECT callback_url FROM webhook_registrations ORDER BY rowid DESC LIMIT 1"
        )
        row = await cur.fetchone()
        await cur.close()
        return row["callback_url"] if row is not None else None
    finally:
        await conn.close()


async def _mark_order_status(order_id: str, status: str, failure_reason: str | None = None) -> None:
    conn = await aiosqlite.connect(DB_PATH)
    try:
        await conn.execute(
            "UPDATE orders SET status = ?, failure_reason = ? WHERE order_id = ?",
            (status, failure_reason, order_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def _deliver_webhook(order_id: str, callback_url: str, payload: dict, *, force_first_attempt_failure: bool) -> None:
    """Deliver the fulfillment callback with bounded retry-with-backoff (FR-006). Runs
    as a background task after the 202 response has already been sent - the purchase
    response must never itself claim fulfillment (spec.md acceptance scenario).

    Delivery always goes over this app's own ASGI transport rather than a real network
    call: ADR-009 is explicit that this webhook harness "has no production equivalent"
    and exists purely for Phase 1's own test/dev verification (the callback_url callers
    register in tests is this sim's own `/__sim__/webhook-sink`). A later feature that
    wants real network delivery to an external, feature-002-owned consumer is a
    deliberate follow-up, not something this Phase 1 harness needs to support."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=5.0) as client:
        for attempt in range(1, WEBHOOK_MAX_ATTEMPTS + 1):
            try:
                if force_first_attempt_failure and attempt == 1:
                    raise httpx.RequestError("X-Sim-Fault: webhook_delay - forcing the first delivery attempt to fail")
                resp = await client.post(callback_url, json=payload)
                if resp.status_code < 300:
                    await _mark_order_status(order_id, "CONFIRMED")
                    log_event("webhook_delivered", sim="ticketing", order_id=order_id, attempt=attempt)
                    return
            except Exception as exc:  # noqa: BLE001 — any delivery failure (network,
                # forced fault, non-2xx) falls through to the bounded retry-with-backoff
                # below; a narrower except would miss real transport failures the retry
                # path exists to handle. Logged so a persistent failure stays observable.
                log_event("webhook_delivery_attempt_failed", sim="ticketing", order_id=order_id, attempt=attempt, error=str(exc))
            if attempt < WEBHOOK_MAX_ATTEMPTS:
                await asyncio.sleep(WEBHOOK_RETRY_BACKOFFS_SECONDS[attempt - 1])
    await _mark_order_status(order_id, "FAILED", failure_reason="webhook delivery exhausted retries")
    log_event("webhook_delivery_exhausted", sim="ticketing", order_id=order_id)


@app.post(PREFIX + "/webhooks/registrations", status_code=201)
async def register_webhook(
    request: Request, _auth: Annotated[str, Depends(check_ticketing_bearer)]
) -> dict:
    """No X-Tenant-Id / Idempotency-Key / Signature parameters are declared on this
    operation in the frozen contract (only the global OAuth2 security requirement
    applies) - implemented exactly as declared, not extended with scoping the contract
    doesn't ask for."""
    body = await request.json()
    conn = await aiosqlite.connect(DB_PATH)
    try:
        await conn.execute(
            "INSERT INTO webhook_registrations (tenant_id, callback_url, events, secret) VALUES (?, ?, ?, ?)",
            ("*", body.get("callback_url", ""), json.dumps(body.get("events", [])), body.get("secret")),
        )
        await conn.commit()
    finally:
        await conn.close()
    return {"registered": True}


@app.post(PREFIX + "/orders", status_code=202)
async def purchase_tickets(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    _sig: Annotated[str, Depends(check_signature)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse:
    body = await request.json()
    endpoint = "POST /orders"
    fp = idempotency.fingerprint(body)
    try:
        replay = await idempotency.check_replay(db, idempotency_key, endpoint, fp)
    except idempotency.IdempotencyKeyConflict:
        return JSONResponse(status_code=409, content=fault("INVENTORY_UNAVAILABLE", "idempotency key reused for a different request"))
    if replay is not None:
        return JSONResponse(status_code=202, content=replay.body)

    for field in ("hold_id", "payment_token", "delivery"):
        if field not in body:
            return JSONResponse(status_code=422, content=fault("INVENTORY_UNAVAILABLE", f"missing {field}"))

    now = datetime.now(UTC)
    is_hold_expired_fault = active_fault(request) == "hold_expired"
    try:
        hold = await holds.confirm_hold(db, body["hold_id"], tenant_id, now=now, force_expired=is_hold_expired_fault)
    except (holds.HoldNotFound, holds.HoldExpiredConflict, holds.HoldTerminalConflict):
        return JSONResponse(status_code=410, content=fault("HOLD_EXPIRED", "hold expired before purchase"))

    order_id = uuid.uuid4().hex
    quantity = hold.resource_ref.get("quantity", 1)
    seats = [f"seat-{i + 1}" for i in range(quantity)]

    cur = await db.execute(
        "SELECT * FROM sections WHERE event_id = ? AND section_id = ? AND tenant_id = ?",
        (hold.resource_ref["event_id"], hold.resource_ref["section_id"], tenant_id),
    )
    section = await cur.fetchone()
    await cur.close()
    total_charged = (section["price_per_seat"] + section["fees_per_seat"]) * quantity if section else 0.0

    callback_url = await _latest_callback_url()

    await db.execute(
        "INSERT INTO orders (order_id, tenant_id, hold_id, event_id, quantity, total_charged, seats, status, "
        "failure_reason, callback_url) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, ?)",
        (order_id, tenant_id, hold.hold_id, hold.resource_ref["event_id"], quantity, total_charged, json.dumps(seats), callback_url),
    )
    await db.commit()

    response_body = {"order_id": order_id, "status": "PENDING", "expected_resolution_seconds": 5}
    await idempotency.store_response(db, idempotency_key, endpoint, fp, 202, response_body)
    log_event("order_placed", sim="ticketing", order_id=order_id, hold_id=hold.hold_id, tenant_id=tenant_id)

    if callback_url:
        webhook_payload = {
            "order_id": order_id,
            "type": "order.confirmed",
            "event_id": hold.resource_ref["event_id"],
            "seats": seats,
            "total_charged": total_charged,
        }
        asyncio.create_task(
            _deliver_webhook(
                order_id, callback_url, webhook_payload,
                force_first_attempt_failure=active_fault(request) == "webhook_delay",
            )
        )

    # The 202 response never claims fulfillment - status is always PENDING here,
    # regardless of how fast delivery happens to complete in the background.
    return JSONResponse(status_code=202, content=response_body)


@app.get(PREFIX + "/orders/{order_id}")
async def get_order(
    order_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM orders WHERE order_id = ? AND tenant_id = ?", (order_id, tenant_id))
    r = await cur.fetchone()
    await cur.close()
    if r is None:
        raise HTTPException(status_code=404, detail="order not found")
    return {
        "order_id": r["order_id"],
        "status": r["status"],
        "event_id": r["event_id"],
        "quantity": r["quantity"],
        "total_charged": r["total_charged"],
        "seats": json.loads(r["seats"]),
        "failure_reason": r["failure_reason"],
    }


@app.post(PREFIX + "/orders/{order_id}/refund")
async def refund_order(
    order_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_ticketing_bearer)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse:
    endpoint = "POST /orders/{order_id}/refund"
    fp = idempotency.fingerprint({"order_id": order_id})
    try:
        replay = await idempotency.check_replay(db, idempotency_key, endpoint, fp)
    except idempotency.IdempotencyKeyConflict:
        return JSONResponse(status_code=409, content=fault("NON_REFUNDABLE", "idempotency key reused for a different request"))
    if replay is not None:
        return JSONResponse(status_code=replay.status_code, content=replay.body)

    cur = await db.execute("SELECT * FROM orders WHERE order_id = ? AND tenant_id = ?", (order_id, tenant_id))
    r = await cur.fetchone()
    await cur.close()
    # Only 202/409 are declared for this operation (no 404) - an order that was never
    # placed is treated the same as a non-refundable one: 409.
    if r is None or r["status"] not in ("CONFIRMED",):
        response = JSONResponse(status_code=409, content=fault("NON_REFUNDABLE", "non-refundable event — requires human"))
        await idempotency.store_response(db, idempotency_key, endpoint, fp, 409, fault("NON_REFUNDABLE", "non-refundable event — requires human"))
        return response

    await db.execute("UPDATE orders SET status = 'REFUNDED' WHERE order_id = ?", (order_id,))
    await db.commit()
    body = {"refund_initiated": True, "order_id": order_id}
    await idempotency.store_response(db, idempotency_key, endpoint, fp, 202, body)
    log_event("order_refund_initiated", sim="ticketing", order_id=order_id, tenant_id=tenant_id)
    return JSONResponse(status_code=202, content=body)
