"""FR-011/SC-001: ticketing-sim contract-conformance suite, driven directly from the
frozen `docs/contracts/openapi/ticketing-sim.openapi.yaml` (T013).

Runs against every operation in the contract, sync and async alike. Until US5 (T034)
adds the async purchase path (`POST /orders`, webhooks) to `sims/ticketing/main.py`,
those routes simply 404 under fuzzing (not a 5xx, so `not_a_server_error` still passes,
and the undocumented-status-code check is already scoped out — see
`contract_helpers.py`). The async path's real correctness property (a callback
delivered later, separate from the 202 response) isn't something per-request schema
fuzzing can observe anyway — that's what the dedicated T035 integration test is for.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from sims.tests.fixtures.contract_helpers import run_contract_suite
from sims.ticketing.main import CLIENT_ID, CLIENT_SECRET, app, compute_signature


def test_ticketing_contract_conformance() -> None:
    with TestClient(app) as client:
        token_resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        )
        access_token = token_resp.json()["access_token"]

    def headers_for_case(case: object) -> dict[str, str]:
        body = getattr(case, "body", None)
        try:
            raw = json.dumps(body).encode("utf-8")
        except TypeError:
            # No request body on this operation (e.g. a GET) — schemathesis represents
            # that with its own `NotSet` sentinel rather than `None`.
            raw = b"{}"
        return {
            "Authorization": f"Bearer {access_token}",
            "X-TM-Signature": compute_signature(raw),
        }

    failures = run_contract_suite(
        contract_filename="ticketing-sim.openapi.yaml",
        app=app,
        headers_for_case=headers_for_case,
    )
    assert failures == [], failures
