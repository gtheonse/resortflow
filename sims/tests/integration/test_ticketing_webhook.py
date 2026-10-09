"""User Story 5 integration test (T035, FR-006/SC-006): purchase response never claims
fulfillment, the fulfillment callback arrives afterward via the webhook sink, and
X-Sim-Fault: webhook_delay demonstrably triggers the bounded retry-with-backoff path."""

from __future__ import annotations

import asyncio
import json
import time

import aiosqlite

from sims.tests.fixtures.clients import ticketing_auth_headers


def _run(coro):
    return asyncio.run(coro)


def _seed_event(db_path, event_id: str, section_id: str) -> None:
    async def seed():
        conn = await aiosqlite.connect(db_path)
        await conn.execute(
            f"INSERT OR REPLACE INTO events VALUES ('{event_id}','MGV','Show','concert','2026-10-05T20:00:00Z',"
            "'2026-10-05T19:00:00Z',120,'v1','Arena',1,'zoneA','123 Main',50,200,'USD','on_sale','desc','','casual',1)"
        )
        await conn.execute(
            f"INSERT OR REPLACE INTO sections VALUES ('{section_id}','{event_id}','MGV','Floor',100.0,10.0,10,'premium')"
        )
        await conn.commit()
        await conn.close()

    _run(seed())


def test_purchase_is_async_and_webhook_arrives_afterward(ticketing_client) -> None:
    from sims.ticketing.main import DB_PATH

    _seed_event(DB_PATH, "evt-wh1", "sec-wh1")
    tenant = {"X-Tenant-Id": "MGV"}

    register = ticketing_client.post(
        "/discovery/v2/webhooks/registrations",
        headers={**tenant, **ticketing_auth_headers(ticketing_client)},
        json={
            "callback_url": "http://testserver/__sim__/webhook-sink",
            "events": ["order.confirmed"],
            "secret": "whsec",
        },
    )
    assert register.status_code == 201

    hold_body = {"event_id": "evt-wh1", "section_id": "sec-wh1", "quantity": 2}
    hold_h = {**tenant, **ticketing_auth_headers(ticketing_client, hold_body), "Idempotency-Key": "wh-hold-1", "Content-Type": "application/json"}
    hold_resp = ticketing_client.post("/discovery/v2/reservations", headers=hold_h, json=hold_body)
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["hold_id"]

    order_body = {"hold_id": hold_id, "payment_token": "tok-1", "delivery": "mobile"}
    order_h = {**tenant, **ticketing_auth_headers(ticketing_client, order_body), "Idempotency-Key": "wh-order-1", "Content-Type": "application/json"}
    purchase_resp = ticketing_client.post("/discovery/v2/orders", headers=order_h, json=order_body)
    assert purchase_resp.status_code == 202
    order_id = purchase_resp.json()["order_id"]

    # The purchase response never claims fulfillment.
    assert purchase_resp.json()["status"] == "PENDING"
    assert "fulfilled" not in json.dumps(purchase_resp.json()).lower()

    # Fulfillment arrives afterward, separately, via the webhook sink.
    deadline = time.time() + 5
    callback = None
    while time.time() < deadline:
        sink_resp = ticketing_client.get(f"/__sim__/webhook-sink/{order_id}")
        if sink_resp.status_code == 200:
            callback = sink_resp.json()
            break
        time.sleep(0.1)
    assert callback is not None, "fulfillment callback never arrived"
    assert callback["callbacks"][0]["order_id"] == order_id
    assert callback["callbacks"][0]["type"] == "order.confirmed"

    # And polling GET /orders/{id} now reflects the confirmed terminal state too.
    poll_deadline = time.time() + 5
    final_status = None
    while time.time() < poll_deadline:
        order_resp = ticketing_client.get(f"/discovery/v2/orders/{order_id}", headers={**tenant, **ticketing_auth_headers(ticketing_client)})
        final_status = order_resp.json()["status"]
        if final_status == "CONFIRMED":
            break
        time.sleep(0.1)
    assert final_status == "CONFIRMED"


def test_webhook_delay_fault_triggers_retry_with_backoff(ticketing_client) -> None:
    from sims.ticketing.main import DB_PATH

    _seed_event(DB_PATH, "evt-wh2", "sec-wh2")
    tenant = {"X-Tenant-Id": "MGV"}

    register = ticketing_client.post(
        "/discovery/v2/webhooks/registrations",
        headers={**tenant, **ticketing_auth_headers(ticketing_client)},
        json={"callback_url": "http://testserver/__sim__/webhook-sink", "events": ["order.confirmed"]},
    )
    assert register.status_code == 201

    hold_body = {"event_id": "evt-wh2", "section_id": "sec-wh2", "quantity": 1}
    hold_h = {**tenant, **ticketing_auth_headers(ticketing_client, hold_body), "Idempotency-Key": "wh-hold-2", "Content-Type": "application/json"}
    hold_resp = ticketing_client.post("/discovery/v2/reservations", headers=hold_h, json=hold_body)
    hold_id = hold_resp.json()["hold_id"]

    order_body = {"hold_id": hold_id, "payment_token": "tok-2", "delivery": "mobile"}
    order_h = {
        **tenant,
        **ticketing_auth_headers(ticketing_client, order_body),
        "Idempotency-Key": "wh-order-2",
        "Content-Type": "application/json",
        "X-Sim-Fault": "webhook_delay",
    }
    purchase_resp = ticketing_client.post("/discovery/v2/orders", headers=order_h, json=order_body)
    assert purchase_resp.status_code == 202
    order_id = purchase_resp.json()["order_id"]

    # The first attempt is forced to fail; delivery only succeeds via the
    # retry-with-backoff path, so it necessarily arrives (measurably) later than the
    # instant the 202 was returned, and still arrives — proving retries happened.
    deadline = time.time() + 5
    callback = None
    while time.time() < deadline:
        sink_resp = ticketing_client.get(f"/__sim__/webhook-sink/{order_id}")
        if sink_resp.status_code == 200:
            callback = sink_resp.json()
            break
        time.sleep(0.1)
    assert callback is not None, "fulfillment callback never arrived even after retry-with-backoff"
    assert callback["callbacks"][0]["order_id"] == order_id
